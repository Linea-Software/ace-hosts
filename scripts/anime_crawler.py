"""Persistent, bounded, robots-aware public-web discovery frontier.

The SQLite state is an untrusted collection of leads. Only independently
verified, maintainer-approved proposals can reach the release pipeline.
All network traffic is HTTPS to pinned, DNS-validated public addresses;
redirects, proxies, authentication and access-control bypass are forbidden.
"""
from __future__ import annotations

# The discovery CLI imports this module lazily and only after its definitions.
# pyright: reportImportCycles=false

import http.client
from html.parser import HTMLParser
import ipaddress
import json
import logging
import socket
import sqlite3
import ssl
import threading
import time
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from scripts.anime_browser import BrowserRenderer
from scripts.anime_discovery import (
    Evidence, SitePage, is_shared,
    normalize_hostname, safe_hostname, site_categories,
)
from scripts.anime_search import parse_brave_results, search_queries

from scripts.anime_discovery import _languages  # pyright: ignore[reportPrivateUsage]

logger = logging.getLogger("ace-hosts.anime-crawler")
AGENT = "ace-hosts-discovery/1.0"
# Indexes are only leads, never independent verification. Operators can add
# licensed, permitted directories with --crawl-directory-url.
PUBLIC_INDEXES: tuple[str, ...] = (
    "https://en.wikipedia.org/wiki/Webtoon",
    "https://en.wikipedia.org/wiki/Manga",
    "https://github.com/topics/manga-reader",
)
MAX_HTML = 650_000
MAX_ROBOTS = 100_000
MAX_LINKS_PER_PAGE = 75
MAX_ANCHORS_PER_PAGE = 3200


class LinkCollector(HTMLParser):
    """Inspect bounded anchor text so navigation links don't monopolize leads."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str = ""
        self._label: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:  # pyright: ignore[reportImplicitOverride]
        if tag == "a" and len(self.links) < MAX_ANCHORS_PER_PAGE:
            self._href = (dict(attrs).get("href") or "")[:2048]
            self._label = []

    def handle_data(self, data: str) -> None:  # pyright: ignore[reportImplicitOverride]
        if self._href and len(self._label) < 5:
            self._label.append(data[:100])

    def handle_endtag(self, tag: str) -> None:  # pyright: ignore[reportImplicitOverride]
        if tag == "a" and self._href:
            self.links.append((self._href, " ".join(self._label)[:250]))
            self._href = ""
            self._label = []


def ranked_links(html: str, page_url: str, limit: int = MAX_LINKS_PER_PAGE) -> tuple[str, ...]:
    """Prioritize topical outbound leads and topical internal directory pages."""
    collector = LinkCollector()
    collector.feed(html)
    host = urlsplit(page_url).hostname
    scores: dict[str, int] = {}
    for href, label in collector.links:
        url = normalize_url(urljoin(page_url, href))
        if not url:
            continue
        target = urlsplit(url).hostname
        if target is None or (target != host and is_shared(target)):
            continue
        thematic = bool(site_categories(unquote(url) + " " + label))
        if target == host:
            if not site_categories(unquote(urlsplit(url).path)):
                continue
            score = 45 + (25 if thematic else 0)
        else:
            score = 80 if thematic else 20
        scores[url] = max(score, scores.get(url, 0))
    # Prefer unseen thematic external sites over navigation links. Low-score
    # external links remain leads, subject to independent page inspection.
    return tuple(url for url, _score in sorted(
        scores.items(), key=lambda item: (-item[1], item[0]))[:limit])


def normalize_url(raw: str) -> str | None:
    """Validate untrusted URLs, including Unicode IDNs and percent-encoded paths."""
    if not raw or len(raw) > 2048 or any(ord(ch) < 32 for ch in raw):
        return None
    try:
        value = urlsplit(raw.strip())
        if (value.scheme != "https" or value.username or value.password
                or value.port not in (None, 443)):
            return None
        host = normalize_hostname(raw)
        if host is None:
            return None
        path = quote(unquote(value.path or "/"), safe="/:@!$&'()*+,;=-._~%")
        # Query strings may be used for navigation but are bounded; strip
        # common tracking parameters to collapse duplicate crawl URLs.
        query = "&".join(p for p in value.query.split("&")
                         if p and not p.lower().startswith(("utm_", "fbclid=", "gclid=")))[:240]
        return urlunsplit(("https", host, path, query, ""))
    except (ValueError, UnicodeError):
        return None


def resolved_public_addresses(host: str) -> tuple[str, ...]:
    """Reject an entire DNS answer set if even one address is non-global."""
    answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    ips = tuple(sorted({str(item[4][0]) for item in answers}))
    if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
        raise ValueError("DNS resolved to a non-public address")
    return ips


@dataclass(frozen=True)
class PageResponse:
    status: int
    content_type: str
    body: bytes


class RequestBudget:
    def __init__(self, max_requests: int, max_seconds: float) -> None:
        self.limit: int = max_requests
        self.deadline: float = time.monotonic() + max_seconds
        self.requests: int = 0
        self._lock: threading.Lock = threading.Lock()

    def consume(self) -> bool:
        with self._lock:
            if self.requests >= self.limit or time.monotonic() >= self.deadline:
                return False
            self.requests += 1
            return True

    def remaining(self) -> bool:
        with self._lock:
            return self.requests < self.limit and time.monotonic() < self.deadline


def safe_get(url: str, budget: RequestBudget, *, timeout: float = 5,
             max_bytes: int = MAX_HTML, headers: dict[str, str] | None = None) -> PageResponse:
    """HTTPS with pinned public IP, SNI/certificate for original host, no redirects.

    http.client's connection factory receives an already-vetted IP; it cannot
    perform a second DNS lookup that rebounds to private infrastructure.
    """
    normalized = normalize_url(url)
    if normalized is None:
        raise ValueError("invalid or unsafe HTTPS URL")
    parts = urlsplit(normalized)
    host = parts.hostname
    if host is None:
        raise ValueError("missing hostname")
    addresses = resolved_public_addresses(host)
    if not budget.consume():
        raise RuntimeError("request or duration budget exhausted")
    # HTTPSConnection still uses 'host' for SNI and certificate verification.
    connection = http.client.HTTPSConnection(
        host, timeout=timeout, context=ssl.create_default_context()
    )
    ip = addresses[0]

    def pinned_socket(_address: tuple[str, int], timeout: float | None = None,
                      source_address: tuple[str, int] | None = None) -> socket.socket:
        return socket.create_connection((ip, 443), timeout=timeout,
                                        source_address=source_address)
    setattr(connection, "_create_connection", pinned_socket)
    try:
        target = parts.path + (("?" + parts.query) if parts.query else "")
        request_headers = {"User-Agent": AGENT, "Accept": "text/html,application/xml;q=0.9,*/*;q=0.1"}
        if headers:
            request_headers.update(headers)
        connection.request("GET", target, headers=request_headers)
        response = connection.getresponse()
        if int(response.headers.get("content-length", "0") or "0") > max_bytes:
            raise ValueError("response declared too large")
        data = response.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError("response exceeded byte limit")
        return PageResponse(response.status, response.headers.get("content-type", ""), data)
    finally:
        connection.close()


@dataclass(frozen=True)
class CrawlConfig:
    state_path: Path
    max_requests: int = 220
    max_seconds: int = 600
    max_depth: int = 3
    max_pages_domain: int = 3
    concurrency: int = 4
    delay: float = 0.6
    timeout: float = 6
    max_browser: int = 8
    max_search_queries: int = 24
    max_seed_hosts: int = 90

    def validate(self) -> None:
        if (not 1 <= self.max_requests <= 10_000 or
                not 1 <= self.max_seconds <= 7200 or
                not 0 <= self.max_depth <= 6 or
                not 1 <= self.max_pages_domain <= 20 or
                not 1 <= self.concurrency <= 12 or
                not 0 <= self.delay <= 60 or
                not 1 <= self.timeout <= 30 or
                not 0 <= self.max_browser <= 30 or
                not 0 <= self.max_search_queries <= 300 or
                not 0 <= self.max_seed_hosts <= 10_000):
            raise ValueError("invalid bounded crawler configuration")


@dataclass(frozen=True)
class CrawlPage:
    url: str
    depth: int
    source: str
    kind: str


class Frontier:
    """All state mutations happen on the parent thread and are committed early."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn: sqlite3.Connection = sqlite3.connect(path)
        self.duplicate_urls: int = 0
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS frontier (
                url TEXT PRIMARY KEY, host TEXT NOT NULL, depth INTEGER NOT NULL,
                priority INTEGER NOT NULL, source TEXT NOT NULL,
                kind TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                error TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS frontier_pending
                ON frontier(status, priority DESC, depth);
            CREATE TABLE IF NOT EXISTS evidence (
                host TEXT NOT NULL, category TEXT NOT NULL,
                repository TEXT NOT NULL, field TEXT NOT NULL,
                origin TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(host, category, repository, field, origin)
            );
        """)
        self.conn.execute("UPDATE frontier SET status='pending' WHERE status='inflight'")
        self.conn.commit()

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()

    def add(self, url: str, *, depth: int, source: str,
            kind: str = "lead", priority: int = 20) -> bool:
        normalized = normalize_url(url)
        if normalized is None or depth > 6:
            return False
        host = urlsplit(normalized).hostname
        if not host or (kind != "directory" and is_shared(host)):
            return False
        with self.conn:
            cursor = self.conn.execute(
                "INSERT OR IGNORE INTO frontier(url,host,depth,priority,source,kind) " +
                "VALUES (?,?,?,?,?,?)", (normalized, host, depth, priority, source[:250], kind)
            )
        if cursor.rowcount == 0:
            self.duplicate_urls += 1
        return cursor.rowcount > 0

    def evidence(self, entry: Evidence) -> None:
        if not safe_hostname(entry.hostname):
            return
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO evidence VALUES(?,?,?,?,?,?)",
                (entry.hostname, entry.category, entry.repository, entry.field,
                 (entry.origin or "")[:500], json.dumps(asdict(entry), ensure_ascii=False)),
            )

    def claim(self, limit: int, max_pages_domain: int) -> list[CrawlPage]:
        rows = cast(list[tuple[str, str, int, str, str]], self.conn.execute(
            "SELECT url,host,depth,source,kind FROM frontier WHERE status='pending' " +
            "ORDER BY priority DESC, depth ASC, url ASC LIMIT 300"
        ).fetchall())
        chosen: list[CrawlPage] = []
        busy: set[str] = set()
        for url, host, depth, source, kind in rows:
            if host in busy:
                continue
            count = cast(int, self.conn.execute(
                "SELECT COUNT(*) FROM frontier WHERE host=? AND status IN ('done','inflight','failed')",
                (host,)
            ).fetchone()[0])
            if count >= max_pages_domain:
                self.conn.execute("UPDATE frontier SET status='skipped',error='per-host page budget' WHERE url=?",
                                  (url,))
                continue
            chosen.append(CrawlPage(url, depth, source, kind))
            busy.add(host)
            self.conn.execute("UPDATE frontier SET status='inflight' WHERE url=?", (url,))
            if len(chosen) >= limit:
                break
        self.conn.commit()
        return chosen

    def complete(self, page: CrawlPage, error: str = "") -> None:
        status = "pending" if error == "budget-exhausted" else "failed" if error else "done"
        with self.conn:
            self.conn.execute("UPDATE frontier SET status=?,error=? WHERE url=?",
                              (status, error[:240], page.url))

    def records(self) -> list[Evidence]:
        rows = cast(list[tuple[str]], self.conn.execute(
            "SELECT payload FROM evidence ORDER BY host,category").fetchall())
        result: list[Evidence] = []
        for (payload,) in rows:
            data = cast(dict[str, object], json.loads(payload))
            langs = data.get("languages")
            result.append(Evidence(
                hostname=cast(str, data["hostname"]), name=cast(str | None, data["name"]),
                category=cast(str, data["category"]),
                languages=tuple(cast(list[str], langs)) if isinstance(langs, list) else (),
                repository=cast(str, data["repository"]), extension_id=None,
                family="web-lead", field=cast(str, data["field"]),
                origin=cast(str | None, data.get("origin")),
            ))
        return result

    def statistics(self) -> dict[str, object]:
        statuses = dict(cast(list[tuple[str, int]], self.conn.execute(
            "SELECT status,COUNT(*) FROM frontier GROUP BY status").fetchall()))
        hosts = cast(int, self.conn.execute("SELECT COUNT(DISTINCT host) FROM frontier").fetchone()[0])
        leads = cast(int, self.conn.execute("SELECT COUNT(DISTINCT host) FROM evidence").fetchone()[0])
        sources = dict(cast(list[tuple[str, int]], self.conn.execute(
            "SELECT source,COUNT(*) FROM frontier GROUP BY source ORDER BY COUNT(*) DESC LIMIT 25"
        ).fetchall()))
        categories = dict(cast(list[tuple[str, int]], self.conn.execute(
            "SELECT category,COUNT(DISTINCT host) FROM evidence GROUP BY category"
        ).fetchall()))
        return {"urls_discovered": sum(statuses.values()), "urls_crawled": statuses.get("done", 0),
                "urls_failed": statuses.get("failed", 0),
                "urls_pending": statuses.get("pending", 0),
                "unique_domains": hosts, "evidence_domains": leads,
                "statuses": statuses, "sources": sources, "categories": categories,
                "duplicate_urls": self.duplicate_urls}


@dataclass(frozen=True)
class InspectedPage:
    page: CrawlPage
    title: str
    categories: tuple[str, ...]
    languages: tuple[str, ...]
    links: tuple[str, ...]
    sitemap_paths: tuple[str, ...]
    error: str = ""
    needs_browser: bool = False


class WebCrawler:
    def __init__(self, config: CrawlConfig,
                 fetch: Callable[..., PageResponse] = safe_get) -> None:
        config.validate()
        self.config: CrawlConfig = config
        self.frontier: Frontier = Frontier(config.state_path)
        self.budget: RequestBudget = RequestBudget(config.max_requests, config.max_seconds)
        self.fetch: Callable[..., PageResponse] = fetch
        self._robots: dict[str, RobotFileParser | None] = {}
        self._robots_lock: threading.Lock = threading.Lock()
        self._robots_guards: dict[str, threading.Lock] = {}
        self._last_host: dict[str, float] = {}
        self._host_delay: dict[str, float] = {}
        self._host_lock: threading.Lock = threading.Lock()
        self.retries: int = 0
        self.errors: Counter[str] = Counter()
        self.browser_rendered: int = 0
        self.started: float = time.monotonic()

    def close(self) -> None:
        self.frontier.close()

    def seed(self, records: list[Evidence], *, directories: tuple[str, ...] = (),
             with_public_indexes: bool = True) -> None:
        for url in ((PUBLIC_INDEXES if with_public_indexes else ()) + directories):
            self.frontier.add(url, depth=0, source="public-index", kind="directory", priority=100)
        seen: set[str] = set()
        for evidence in records:
            if len(seen) >= self.config.max_seed_hosts:
                break
            host = evidence.hostname
            if host not in seen and safe_hostname(host):
                seen.add(host)
                self.frontier.add("https://" + host + "/", depth=0,
                                  source="catalog:" + evidence.repository, priority=35)

    def _request(self, url: str, *, max_bytes: int = MAX_HTML,
                 headers: dict[str, str] | None = None) -> PageResponse:
        """Bounded per-host pacing and one retry for transient server failures."""
        host = urlsplit(url).hostname or ""
        for attempt in range(2):
            with self._host_lock:
                now = time.monotonic()
                due = self._last_host.get(host, 0.0)
                pause = max(0.0, due - now)
                self._last_host[host] = max(due, now) + self._host_delay.get(
                    host, self.config.delay)
            if pause:
                time.sleep(pause)
            try:
                response = self.fetch(url, self.budget, timeout=self.config.timeout,
                                      max_bytes=max_bytes, headers=headers)
            except (socket.timeout, TimeoutError, ConnectionError):
                if attempt == 0 and self.budget.remaining():
                    self.retries += 1
                    time.sleep(0.5)
                    continue
                raise
            if response.status in {500, 502, 503, 504} and attempt == 0 and self.budget.remaining():
                self.retries += 1
                time.sleep(0.5)
                continue
            # Never retry 401/403/429: an access-control response is final.
            return response
        raise RuntimeError("retry budget exhausted")

    def search(self, token: str | None,
               templates: dict[str, str] | None = None) -> None:
        if not token or self.config.max_search_queries == 0:
            return
        for lang, query in search_queries(max_queries=self.config.max_search_queries,
                                          templates=templates):
            if not self.budget.remaining():
                break
            url = ("https://api.search.brave.com/res/v1/web/search?q="
                   + quote(query, safe="") + "&count=20")
            try:
                result = self._request(url, max_bytes=250_000,
                                       headers={"X-Subscription-Token": token, "Accept": "application/json"})
                if result.status != 200:
                    self.errors[f"search-http-{result.status}"] += 1
                    continue
                for address, title in parse_brave_results(cast(object, json.loads(result.body))):
                    host = normalize_hostname(address)
                    if host and safe_hostname(host):
                        self.frontier.add("https://" + host + "/", depth=0,
                                          source="brave-search:" + lang, priority=85)
                        self.frontier.evidence(Evidence(host, title or None, "manga", (lang,),
                                                        "brave-search", None, "web-lead", "url",
                                                        origin=address))
            except (OSError, ValueError, RuntimeError, http.client.HTTPException,
                    ssl.SSLError, json.JSONDecodeError) as error:
                self.errors[type(error).__name__] += 1

    def _allowed(self, url: str) -> bool:
        host = urlsplit(url).hostname
        if host is None:
            return False
        with self._robots_lock:
            guard = self._robots_guards.setdefault(host, threading.Lock())
        with guard:
            if host not in self._robots:
                robots_url = "https://" + host + "/robots.txt"
                try:
                    response = self._request(robots_url, max_bytes=MAX_ROBOTS)
                    if response.status in (401, 403, 429) or response.status >= 500:
                        return False
                    parser: RobotFileParser | None = None
                    if response.status == 200:
                        parser = RobotFileParser()
                        parser.parse(response.body.decode("utf-8", "replace").splitlines())
                        delay = parser.crawl_delay(AGENT)
                        if delay is not None:
                            with self._host_lock:
                                self._host_delay[host] = max(self.config.delay, min(float(delay), 60))
                    elif response.status != 404:
                        return False
                    self._robots[host] = parser
                except (OSError, ValueError, RuntimeError, http.client.HTTPException,
                        ssl.SSLError):
                    return False
            robots = self._robots[host]
        return robots is None or robots.can_fetch(AGENT, url)

    def _inspect(self, page: CrawlPage) -> InspectedPage:
        host = urlsplit(page.url).hostname or ""
        if not self._allowed(page.url):
            reason = "budget-exhausted" if not self.budget.remaining() else "robots denied or unavailable"
            return InspectedPage(page, "", (), (), (), (), reason)
        try:
            response = self._request(page.url)
            if response.status in (401, 403, 429):
                return InspectedPage(page, "", (), (), (), (), "access denied")
            if response.status != 200:
                return InspectedPage(page, "", (), (), (), (), "http-" + str(response.status))
            if "html" not in response.content_type.lower():
                return InspectedPage(page, "", (), (), (), (), "not HTML")
            text = response.body.decode("utf-8", "replace")
            parser = SitePage()
            parser.feed(text)
            categories = site_categories(parser.topic_text)
            links = ranked_links(text, page.url)
            paths: list[str] = []
            # One permitted sitemap per domain. It supplies path hints only,
            # not independent verification and never unbounded sitemap indexes.
            if page.depth == 0 and page.kind != "directory":
                sitemap = "https://" + host + "/sitemap.xml"
                if self._allowed(sitemap) and self.budget.remaining():
                    try:
                        sitemap_data = self._request(sitemap, max_bytes=MAX_HTML)
                        if sitemap_data.status == 200 and "xml" in sitemap_data.content_type.lower():
                            root = ET.fromstring(sitemap_data.body)
                            if root.tag.rsplit("}", 1)[-1] == "urlset":
                                for element in root.iter():
                                    if len(paths) >= 8:
                                        break
                                    if element.tag.rsplit("}", 1)[-1] == "loc" and element.text:
                                        loc = normalize_url(element.text)
                                        if loc and urlsplit(loc).hostname == host and site_categories(
                                                unquote(urlsplit(loc).path)):
                                            paths.append(loc)
                    except (OSError, ValueError, RuntimeError, ET.ParseError, http.client.HTTPException):
                        pass
            return InspectedPage(page, parser.title, categories, _languages(parser.language),
                                 links, tuple(paths), needs_browser=not categories and page.depth == 0
                                 and page.kind != "directory")
        except (OSError, ValueError, RuntimeError, http.client.HTTPException,
                ssl.SSLError) as error:
            return InspectedPage(page, "", (), (), (), (),
                                 "budget-exhausted" if not self.budget.remaining()
                                 else type(error).__name__)

    def _record(self, data: InspectedPage, browser: BrowserRenderer | None) -> None:
        page = data.page
        host = urlsplit(page.url).hostname or ""
        self.frontier.complete(page, data.error)
        if data.error:
            self.errors[data.error] += 1
            return
        categories = data.categories
        languages = data.languages
        title = data.title
        method = "html-title-description"
        if data.needs_browser and browser is not None and self.budget.remaining():
            # The browser is a conservative fallback, never a bypass for denied
            # access. Its route guard permits same-origin HTTPS only.
            html = browser.render(host, timeout=self.config.timeout)
            if html:
                parser = SitePage()
                parser.feed(html)
                categories = site_categories(parser.topic_text)
                languages = _languages(parser.language) or languages
                title = parser.title or title
                method = "html-rendered"
                self.browser_rendered += 1
        if categories and page.kind != "directory" and safe_hostname(host):
            for category in categories:
                self.frontier.evidence(Evidence(host, title or None, category, languages,
                                               "site-homepage", None, "web-lead", method,
                                               origin=page.url))
        for loc in data.sitemap_paths:
            self.frontier.add(loc, depth=page.depth + 1,
                              source="sitemap:" + host, priority=25)
        if page.depth >= self.config.max_depth or not (categories or page.kind == "directory"):
            return
        for href in data.links:
            url = normalize_url(urljoin(page.url, href))
            if not url:
                continue
            target = urlsplit(url).hostname
            if target is None:
                continue
            if target == host:
                if site_categories(unquote(urlsplit(url).path)):
                    self.frontier.add(url, depth=page.depth + 1, source="internal:" + host,
                                      priority=15)
            elif safe_hostname(target):
                # Every external link remains a lead pending target verification.
                self.frontier.add("https://" + target + "/", depth=page.depth + 1,
                                  source="outbound:" + host, priority=55)
                self.frontier.evidence(Evidence(target, None, "manga", (), "web-link", None,
                                               "web-lead", "href", origin=page.url))

    def run(self) -> dict[str, object]:
        browser = BrowserRenderer(max_sites=self.config.max_browser) if self.config.max_browser else None
        try:
            with ThreadPoolExecutor(max_workers=self.config.concurrency) as workers:
                while self.budget.remaining():
                    batch = self.frontier.claim(self.config.concurrency,
                                                self.config.max_pages_domain)
                    if not batch:
                        break
                    results = list(workers.map(self._inspect, batch))
                    for result in results:
                        self._record(result, browser)
        finally:
            if browser is not None:
                browser.close()
        stats = self.frontier.statistics()
        records = self.frontier.records()
        stats.update({
            "requests": self.budget.requests,
            "request_budget": self.config.max_requests,
            "duration_seconds": round(time.monotonic() - self.started, 2),
            "browser_rendered": self.browser_rendered,
            "retry_count": self.retries,
            "errors": dict(self.errors),
            "websites_verified": len({e.hostname for e in records if e.repository == "site-homepage"}),
            "language_distribution": dict(Counter(lang for e in records for lang in e.languages)),
        })
        return stats


def crawl_web(records: list[Evidence], config: CrawlConfig, *,
              directories: tuple[str, ...] = (), public_indexes: bool = True,
              search_token: str | None = None,
              search_templates: dict[str, str] | None = None
              ) -> tuple[list[Evidence], dict[str, object]]:
    crawler = WebCrawler(config)
    try:
        crawler.seed(records, directories=directories, with_public_indexes=public_indexes)
        crawler.search(search_token, templates=search_templates)
        stats = crawler.run()
        return crawler.frontier.records(), stats
    finally:
        crawler.close()
