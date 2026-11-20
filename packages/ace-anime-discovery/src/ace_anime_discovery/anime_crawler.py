"""Persistent, bounded, robots-aware public-web discovery frontier.

The SQLite state is an untrusted collection of leads. Only independently
verified, maintainer-approved proposals can reach the release pipeline.
All network traffic is HTTPS to pinned, DNS-validated public addresses;
cross-host redirects become fresh, robots-checked leads; proxies, authentication
and access-control bypass are forbidden.
"""
from __future__ import annotations

# The discovery CLI imports this module lazily and only after its definitions.
# pyright: reportImportCycles=false
import hashlib
import http.client
import ipaddress
import json
import logging
import re
import socket
import sqlite3
import ssl
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Callable, Collection, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup

from ace_anime_discovery.anime_browser import BrowserRenderer
from ace_anime_discovery.anime_content_verification import (
    ContentVerificationResult,
    verify_content_page,
)
from ace_anime_discovery.anime_crawl_policy import public_crawl_url
from ace_anime_discovery.anime_diagnostics import source_group
from ace_anime_discovery.anime_run_report import RunReport
from ace_anime_discovery.anime_discovery import (
    Evidence,
    SitePage,
    _languages,  # pyright: ignore[reportPrivateUsage]
    content_page_path,
    is_shared,
    normalize_hostname,
    safe_hostname,
    site_categories,
)
from ace_anime_discovery.anime_search import parse_brave_results, search_queries

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
OPAQUE_CONTENT_ANCHOR = re.compile(
    r"\b(?:read|watch|chapters?|episodes?|manga|manhwa|manhua|webtoon|"
    + r"novels?|scanlation|capitoli?|chapitres?)\b|第\s*\d+\s*[話话章回]",
    re.IGNORECASE,
)
# Do not treat access-denied and error pages as content verification.
EMPTY_PAGE_TITLE = re.compile(
    r"^(?:404|403|not found|access denied|just a moment|error|"
    + r"under construction|coming soon)", re.IGNORECASE,
)
MISSING_ROBOTS_TITLE = re.compile(
    r"\b(?:404|410)\b|(?:page|file|resource)\s+not\s+found|"
    + r"not\s+found|does\s+not\s+exist", re.IGNORECASE,
)


def missing_robots_html(body: bytes) -> bool:
    """Recognize only an explicit HTML not-found page, never a challenge."""
    sample = body[:8192].decode("utf-8", "replace")
    title = re.search(
        r"<title(?:\s+[^>]*)?>([^<]{1,200})</title\s*>",
        sample, re.IGNORECASE,
    )
    return bool(title and MISSING_ROBOTS_TITLE.search(title.group(1)))


class LinkCollector:
    """Collect capped anchor targets and visible labels from parsed HTML."""

    def __init__(self) -> None:
        self.links: list[tuple[str, str]] = []

    def feed(self, html: str, *, parsed_soup: BeautifulSoup | None = None) -> None:
        soup = parsed_soup if parsed_soup is not None else BeautifulSoup(html[:MAX_HTML], "lxml")
        self.links = [
            (str(link.get("href") or "")[:2048], link.get_text(" ", strip=True)[:250])
            for link in soup.find_all("a", href=True, limit=MAX_ANCHORS_PER_PAGE)
        ]


def ranked_links(html: str, page_url: str, limit: int = MAX_LINKS_PER_PAGE, *,
                 allow_generic_external: bool = False,
                 allow_opaque_internal: bool = False,
                 parsed_soup: BeautifulSoup | None = None) -> tuple[str, ...]:
    """Follow topical links; bound generic outbound links to trusted directories."""
    if limit <= 0:
        return ()
    collector = LinkCollector()
    collector.feed(html, parsed_soup=parsed_soup)
    host = urlsplit(page_url).hostname
    scores: dict[str, int] = {}
    for href, label in collector.links:
        url = normalize_url(urljoin(page_url, href))
        if not url or not public_crawl_url(url):
            continue
        target = urlsplit(url).hostname
        if target is None or (target != host and is_shared(target)):
            continue
        thematic = bool(site_categories(unquote(url) + " " + label))
        if target == host:
            recognized = bool(site_categories(unquote(urlsplit(url).path))
                              or content_page_path(urlsplit(url).path))
            if recognized:
                score = 45 + (25 if thematic else 0)
            elif (allow_opaque_internal and urlsplit(url).path != "/"
                  and OPAQUE_CONTENT_ANCHOR.search(label)):
                # Unknown CMS routes are leads, not proof. Allow only a small
                # number per verified topical homepage to limit request cost.
                score = 30
            else:
                continue
        else:
            if not thematic and not allow_generic_external:
                # A random link from an unverified homepage must not trigger
                # a cascade across unrelated sites or SEO link farms.
                continue
            score = 80 if thematic else 20
        scores[url] = max(score, scores.get(url, 0))
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    # Allow only a handful of generic links on explicitly seeded directories.
    result: list[str] = []
    generic = 0
    opaque = 0
    for url, score in ranked:
        if score == 30:
            if opaque >= 6:
                continue
            opaque += 1
        if score == 20:
            if generic >= 5:
                continue
            generic += 1
        result.append(url)
        if len(result) >= limit:
            break
    return tuple(result)


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
        query_text = "&".join(
            p for p in value.query.split("&")
            if p and not p.lower().startswith(("utm_", "fbclid=", "gclid="))
        )[:240]
        query = quote(query_text, safe=":/?&=;+%@!$'()*,-._~")[:720]
        # Never publish an incomplete percent escape after the byte cap.
        last_escape = query.rfind("%")
        if last_escape >= len(query) - 2:
            query = query[:last_escape]
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
    location: str = ""


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
        return PageResponse(
            response.status, response.headers.get("content-type", ""), data,
            response.headers.get("location", "")[:2048],
        )
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
    max_seed_hosts: int = 1200
    max_joined_hosts: int = 1500
    report_path: Path | None = None

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
                not 0 <= self.max_seed_hosts <= 10_000 or
                not 0 <= self.max_joined_hosts <= 10_000):
            raise ValueError("invalid bounded crawler configuration")


@dataclass(frozen=True)
class CrawlPage:
    url: str
    depth: int
    source: str
    kind: str


_DISCOVERY_SOURCE_CLASSES = (
    "catalog", "brave-search", "joined-candidate", "outbound", "other",
)


def _scheduler_source_class(source: str) -> str:
    if source == "verification-homepage":
        return "verification-homepage"
    if source == "catalog":
        return "catalog"
    if source == "joined-candidate":
        return "joined-candidate"
    if source.startswith("brave-search:"):
        return "brave-search"
    if source.startswith("outbound:"):
        return "outbound"
    return "other"


class Frontier:
    """All state mutations happen on the parent thread and are committed early."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn: sqlite3.Connection = sqlite3.connect(path)
        self.duplicate_urls: int = 0
        self.queued_this_run: Counter[str] = Counter()
        self.enqueue_rejections: Counter[str] = Counter()
        self.queued_hosts: set[str] = set()
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
        # Upgrade existing GitHub Actions cache DBs without losing discovery leads.
        schema_rows = cast(list[tuple[int, str, str, int, object, int]],
                           self.conn.execute("PRAGMA table_info(frontier)").fetchall())
        existing = {row[1] for row in schema_rows}
        for name, kind in (
            ("last_checked", "REAL NOT NULL DEFAULT 0"),
            ("next_check", "REAL NOT NULL DEFAULT 0"),
            ("attempts", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if name not in existing:
                self.conn.execute(f"ALTER TABLE frontier ADD COLUMN {name} {kind}")
        self.conn.execute(
            ("UPDATE frontier SET next_check=? WHERE status IN ('done','failed') "
             + "AND next_check=0"), (time.time(),)
        )
        evidence_columns = {row[1] for row in cast(
            list[tuple[int, str, str, int, object, int]],
            self.conn.execute("PRAGMA table_info(evidence)").fetchall()
        )}
        if "last_seen" not in evidence_columns:
            self.conn.execute(
                "ALTER TABLE evidence ADD COLUMN last_seen REAL NOT NULL DEFAULT 0"
            )
            # Legacy rows have unknown age: do not promote them as freshly
            # verified until a new inspection supplies current evidence.
        self.conn.executescript("""
            CREATE INDEX IF NOT EXISTS frontier_recheck
                ON frontier(status, next_check, priority DESC);
            CREATE TABLE IF NOT EXISTS domains (
                host TEXT PRIMARY KEY, first_seen REAL NOT NULL,
                last_seen REAL NOT NULL, last_verified REAL NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS run_state (
                key TEXT PRIMARY KEY, value INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS input_fingerprints (
                key TEXT PRIMARY KEY, digest TEXT NOT NULL
            );
        """)
        # Earlier versions followed every outbound link from any page with a
        # matching word. Discard that legacy link-farm backlog once; keep
        # catalog, joined-list, search and explicit directory checkpoints.
        version = cast(tuple[int] | None, self.conn.execute(
            "SELECT value FROM run_state WHERE key='outbound_policy_version'"
        ).fetchone())
        if version is None or version[0] < 2:
            with self.conn:
                removed = self.conn.execute(
                    "DELETE FROM frontier WHERE source LIKE 'outbound:%'"
                ).rowcount
                self.conn.execute("DELETE FROM evidence WHERE repository='web-link'")
                self.conn.execute(
                    "DELETE FROM domains WHERE host NOT IN (SELECT host FROM frontier)"
                )
                self.conn.execute(
                    "INSERT OR REPLACE INTO run_state(key,value) VALUES('outbound_policy_version',2)"
                )
            logger.info("crawl migration: removed %d legacy outbound leads", removed)
        # Older cached HTML classifications used substring matches and SEO
        # metadata. Recheck those pages before using them as multi-page proof.
        policy = cast(tuple[int] | None, self.conn.execute(
            "SELECT value FROM run_state WHERE key='site_evidence_policy_version'"
        ).fetchone())
        if policy is None or policy[0] < 4:
            with self.conn:
                stale = self.conn.execute(
                    "DELETE FROM evidence WHERE repository IN ('site-homepage','site-internal-page')"
                ).rowcount
                self.conn.execute(
                    "UPDATE frontier SET next_check=? WHERE status='done' AND "
                    + "(url = 'https://' || host || '/' OR source LIKE 'internal:%' "
                    + "OR source LIKE 'sitemap:%')",
                    (time.time(),),
                )
                self.conn.execute(
                    "INSERT OR REPLACE INTO run_state(key,value) VALUES('site_evidence_policy_version',4)"
                )
            logger.info("crawl migration: invalidated %d stale site observations", stale)
        self.conn.execute("CREATE TEMP TABLE exhausted_hosts(host TEXT PRIMARY KEY)")
        self.conn.execute("CREATE TEMP TABLE claimed_batch(host TEXT PRIMARY KEY)")
        self._claimed_per_host: Counter[str] = Counter()
        recovered = self.conn.execute(
            "UPDATE frontier SET status='pending' WHERE status='inflight'"
        ).rowcount
        self._init_scheduler()
        if recovered:
            # Interrupted requests must not be skipped by persisted keyset
            # cursors that advanced before those pages were completed.
            self.conn.execute(
                "UPDATE frontier_scheduler_cursor SET last_host='',last_due=0"
            )
            self.conn.execute(
                "DELETE FROM run_state WHERE key='frontier_scheduler_turn'"
            )
        self.conn.commit()

    def _init_scheduler(self) -> None:
        """Maintain indexed queue heads per host and crawl stage."""
        column_rows = cast(list[tuple[int, str, str, int, object, int, int]],
                           self.conn.execute(
                               "PRAGMA table_xinfo(frontier)"
                           ).fetchall())
        columns = {row[1] for row in column_rows}
        if "schedule_lane" not in columns:
            self.conn.execute("""
                ALTER TABLE frontier ADD COLUMN schedule_lane INTEGER
                GENERATED ALWAYS AS (
                    CASE WHEN kind='directory' THEN 2
                         WHEN url='https://' || host || '/' THEN 0
                         ELSE 1 END
                ) VIRTUAL
            """)
        self.conn.executescript("""
            CREATE INDEX IF NOT EXISTS frontier_scheduler_pending
                ON frontier(host,status,schedule_lane,priority DESC,depth,url);
            CREATE INDEX IF NOT EXISTS frontier_scheduler_first
                ON frontier(status,schedule_lane,priority DESC,host,depth,url);
            CREATE INDEX IF NOT EXISTS frontier_scheduler_due
                ON frontier(host,next_check,url)
                WHERE status IN ('done','failed','deferred') AND next_check > 0;
            CREATE TABLE IF NOT EXISTS frontier_schedule (
                lane INTEGER NOT NULL, host TEXT NOT NULL,
                url TEXT NOT NULL, ready_at REAL NOT NULL,
                source_class TEXT NOT NULL DEFAULT 'other',
                PRIMARY KEY (lane,host)
            );
            CREATE INDEX IF NOT EXISTS frontier_schedule_due
                ON frontier_schedule(lane,ready_at,host);
            CREATE INDEX IF NOT EXISTS frontier_schedule_host
                ON frontier_schedule(host);
            CREATE TABLE IF NOT EXISTS frontier_scheduler_cursor (
                lane INTEGER PRIMARY KEY,
                last_host TEXT NOT NULL DEFAULT '',
                last_due REAL NOT NULL DEFAULT 0
            );
        """)
        head_columns = cast(list[tuple[int, str, str, int, object, int]],
                            self.conn.execute(
                                "PRAGMA table_info(frontier_schedule)"
                            ).fetchall())
        if "source_class" not in {row[1] for row in head_columns}:
            self.conn.execute(
                "ALTER TABLE frontier_schedule "
                + "ADD COLUMN source_class TEXT NOT NULL DEFAULT 'other'"
            )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS frontier_schedule_class "
            + "ON frontier_schedule(lane,source_class,host)"
        )
        # Stable buckets for the different discovery channels. A per-source
        # keyset rotation prevents a large catalog batch from hiding joined
        # or search leads behind one alphabetically ordered backlog.
        source_sql = (
            "CASE WHEN source='verification-homepage' THEN 'verification-homepage' "
            + "WHEN source='catalog' THEN 'catalog' "
            + "WHEN source='joined-candidate' THEN 'joined-candidate' "
            + "WHEN source LIKE 'brave-search:%' THEN 'brave-search' "
            + "WHEN source LIKE 'outbound:%' THEN 'outbound' "
            + "ELSE 'other' END"
        )

        def refresh(host: str) -> str:
            # Host is the fixed trigger alias NEW or OLD.
            statements = [f"DELETE FROM frontier_schedule WHERE host={host}.host;"]
            for lane in (0, 1, 2):
                statements.append(f"""
                    INSERT INTO frontier_schedule(
                        lane,host,url,ready_at,source_class
                    )
                    SELECT {lane},host,url,0,{source_sql} FROM frontier
                    WHERE host={host}.host AND status='pending'
                      AND schedule_lane={lane}
                    ORDER BY priority DESC,depth,url LIMIT 1;
                """)
            statements.append(f"""
                INSERT INTO frontier_schedule(
                    lane,host,url,ready_at,source_class
                )
                SELECT 3,host,url,next_check,'recheck' FROM frontier
                WHERE host={host}.host AND status IN ('done','failed','deferred')
                  AND next_check > 0
                ORDER BY next_check,url LIMIT 1;
            """)
            return "\n".join(statements)

        self.conn.executescript(f"""
            DROP TRIGGER IF EXISTS frontier_schedule_insert;
            DROP TRIGGER IF EXISTS frontier_schedule_delete;
            DROP TRIGGER IF EXISTS frontier_schedule_update;
            DROP TRIGGER IF EXISTS frontier_schedule_rehost;
            CREATE TRIGGER frontier_schedule_insert
            AFTER INSERT ON frontier BEGIN
                {refresh("NEW")}
            END;
            CREATE TRIGGER frontier_schedule_delete
            AFTER DELETE ON frontier BEGIN
                {refresh("OLD")}
            END;
            CREATE TRIGGER frontier_schedule_update
            AFTER UPDATE OF url,host,kind,status,priority,depth,next_check
            ON frontier WHEN NEW.host=OLD.host BEGIN
                {refresh("NEW")}
            END;
            CREATE TRIGGER frontier_schedule_rehost
            AFTER UPDATE OF url,host,kind,status,priority,depth,next_check
            ON frontier WHEN NEW.host!=OLD.host BEGIN
                {refresh("OLD")}
                {refresh("NEW")}
            END;
        """)

        version = cast(tuple[int] | None, self.conn.execute(
            "SELECT value FROM run_state WHERE key='frontier_scheduler_version'"
        ).fetchone())
        if version is None or version[0] < 2:
            # One-time cache migration; later startups retain the queue heads.
            with self.conn:
                self.conn.execute("DELETE FROM frontier_schedule")
                self.conn.execute(f"""
                    INSERT INTO frontier_schedule(
                        lane,host,url,ready_at,source_class
                    )
                    SELECT lane,host,url,0,source_class FROM (
                        SELECT schedule_lane AS lane,host,url,
                            {source_sql} AS source_class,
                            ROW_NUMBER() OVER (
                                PARTITION BY schedule_lane,host
                                ORDER BY priority DESC,depth,url
                            ) AS rank
                        FROM frontier WHERE status='pending'
                    ) WHERE rank=1
                """)
                self.conn.execute("""
                    INSERT INTO frontier_schedule(
                        lane,host,url,ready_at,source_class
                    )
                    SELECT 3,host,url,next_check,'recheck' FROM (
                        SELECT host,url,next_check,
                            ROW_NUMBER() OVER (
                                PARTITION BY host ORDER BY next_check,url
                            ) AS rank
                        FROM frontier
                        WHERE status IN ('done','failed','deferred') AND next_check>0
                    ) WHERE rank=1
                """)
                self.conn.execute(
                    "INSERT OR REPLACE INTO run_state(key,value) "
                    + "VALUES('frontier_scheduler_version',2)"
                )
        self.conn.executemany(
            "INSERT OR IGNORE INTO frontier_scheduler_cursor(lane) VALUES(?)",
            [(lane,) for lane in range(4)],
        )

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()

    def add(self, url: str, *, depth: int, source: str,
            kind: str = "lead", priority: int = 20) -> bool:
        normalized = normalize_url(url)
        if normalized is None or depth > 6:
            self.enqueue_rejections["invalid_url_or_depth"] += 1
            return False
        if not public_crawl_url(normalized):
            self.enqueue_rejections["sensitive_url"] += 1
            return False
        host = urlsplit(normalized).hostname
        if not host or (kind != "directory" and is_shared(host)):
            self.enqueue_rejections["shared_or_invalid_host"] += 1
            return False
        with self.conn:
            cursor = self.conn.execute(
                "INSERT OR IGNORE INTO frontier(url,host,depth,priority,source,kind) " +
                "VALUES (?,?,?,?,?,?)", (normalized, host, depth, priority, source[:250], kind)
            )
            self.conn.execute(
                ("INSERT INTO domains(host,first_seen,last_seen) VALUES(?,?,?) "
                 "ON CONFLICT(host) DO UPDATE SET last_seen=excluded.last_seen"),
                (host, time.time(), time.time()),
            )
        if cursor.rowcount == 0:
            self.duplicate_urls += 1
            self.enqueue_rejections["duplicate_url"] += 1
        else:
            self.queued_this_run[source_group(source)] += 1
            self.queued_hosts.add(host)
        return cursor.rowcount > 0

    def evidence(self, entry: Evidence) -> None:
        if not safe_hostname(entry.hostname):
            return
        with self.conn:
            self.conn.execute(
                ("INSERT INTO evidence(host,category,repository,field,origin,payload,last_seen) "
                 + "VALUES(?,?,?,?,?,?,?) "
                 + "ON CONFLICT(host,category,repository,field,origin) "
                 + "DO UPDATE SET payload=excluded.payload,last_seen=excluded.last_seen"),
                (entry.hostname, entry.category, entry.repository, entry.field,
                 (entry.origin or "")[:500], json.dumps(asdict(entry), ensure_ascii=False),
                 time.time()),
            )

    def prioritize_homepage(self, host: str, *, now: float | None = None) -> bool:
        """Queue a missing homepage for content-backed human review.

        A recently completed/rejected homepage is not retried early. Newly
        queued homepage checks can exceed the per-run internal-page quota by
        one, but remain subject to robots, network budgets and policy.
        """
        if not safe_hostname(host):
            return False
        if self.conn.execute(
            "SELECT 1 FROM evidence WHERE host=? AND repository='site-homepage' LIMIT 1",
            (host,),
        ).fetchone():
            return False
        url = f"https://{host}/"
        checked = time.time() if now is None else now
        row = cast(tuple[str, float] | None, self.conn.execute(
            "SELECT status,next_check FROM frontier WHERE url=?", (url,),
        ).fetchone())
        if row is not None:
            status, next_check = row
            # Never accelerate failed robots checks or completed visits
            # before their existing retry backoff expires.
            if status == "inflight" or (
                status != "pending" and next_check > checked
            ):
                return False
            with self.conn:
                self.conn.execute(
                    "UPDATE frontier SET status='pending',source='verification-homepage',"
                    + "priority=MAX(priority,95),next_check=0,error='' WHERE url=?",
                    (url,),
                )
            return True
        return self.add(url, depth=0, source="verification-homepage", priority=95)

    def prioritize_promising_homepages(self, *, limit: int = 128) -> int:
        """Resume missing homepage checks for sites with two verified pages."""
        if limit <= 0:
            return 0
        hosts = cast(list[tuple[str]], self.conn.execute(
            "SELECT host FROM evidence WHERE repository='site-internal-page' "
            + "AND field='verified-content-page' GROUP BY host "
            + "HAVING COUNT(DISTINCT origin)>=2 ORDER BY host LIMIT ?",
            (limit * 4,),
        ).fetchall())
        selected = 0
        for (host,) in hosts:
            if self.prioritize_homepage(host):
                selected += 1
                if selected >= limit:
                    break
        return selected

    def clear_homepage_evidence(self, host: str) -> None:
        """Successful rechecks replace old homepage classifications."""
        with self.conn:
            self.conn.execute(
                "DELETE FROM evidence WHERE host=? AND repository='site-homepage'",
                (host,),
            )

    def clear_page_evidence(self, host: str, url: str) -> None:
        """Do not trust an internal page after a failed or changed recheck."""
        with self.conn:
            self.conn.execute(
                "DELETE FROM evidence WHERE host=? AND repository='site-internal-page' AND origin=?",
                (host, url),
            )

    def _next_scheduled_row(
        self, lane: int, now: float, *, source_class: str | None = None,
    ) -> tuple[str, str, int, str, str, float] | None:
        """Seek the next eligible host (or due timestamp) using queue indexes."""
        cursor = cast(tuple[str, float], self.conn.execute(
            "SELECT last_host,last_due FROM frontier_scheduler_cursor WHERE lane=?",
            (lane,),
        ).fetchone())
        if lane != 3 and not cursor[0] and source_class is None:
            # Keep initial high-priority seeds first; subsequent picks rotate
            # through hosts, so priority cannot starve lower-ranked hosts.
            first = cast(tuple[str, str, int, str, str, float] | None,
                         self.conn.execute("""
                SELECT f.url,f.host,f.depth,f.source,f.kind,0.0
                FROM frontier AS f
                WHERE f.status='pending' AND f.schedule_lane=?
                  AND NOT EXISTS(
                      SELECT 1 FROM exhausted_hosts AS e WHERE e.host=f.host
                  )
                  AND NOT EXISTS(
                      SELECT 1 FROM claimed_batch AS b WHERE b.host=f.host
                  )
                ORDER BY f.priority DESC,f.host,f.depth,f.url LIMIT 1
            """, (lane,)).fetchone())
            if first is not None:
                return first

        base = """
            SELECT f.url,f.host,f.depth,f.source,f.kind,s.ready_at
            FROM frontier_schedule AS s
            JOIN frontier AS f ON f.url=s.url
            WHERE s.lane=?
              AND (s.source_class='verification-homepage' OR NOT EXISTS(
                  SELECT 1 FROM exhausted_hosts AS e WHERE e.host=s.host
              ))
              AND NOT EXISTS(
                  SELECT 1 FROM claimed_batch AS b WHERE b.host=s.host
              )
        """
        args_prefix: tuple[object, ...] = (lane,)
        if source_class is not None:
            base += " AND s.source_class=?"
            args_prefix += (source_class,)
        for after in (True, False):
            if lane == 3:
                suffix = " AND s.ready_at<=?"
                args: tuple[object, ...] = args_prefix + (now,)
                if after:
                    suffix += " AND (s.ready_at,s.host)>(?,?)"
                    args += (cursor[1], cursor[0])
                suffix += " ORDER BY s.ready_at,s.host LIMIT 1"
            else:
                suffix = " AND s.host>?" if after else ""
                args = args_prefix + (cursor[0],) if after else args_prefix
                suffix += " ORDER BY s.host LIMIT 1"
            row = cast(tuple[str, str, int, str, str, float] | None,
                       self.conn.execute(base + suffix, args).fetchone())
            if row is not None:
                return row
        return None

    def claim(self, limit: int, max_pages_domain: int) -> list[CrawlPage]:
        """Rotate four stages and host cursors without ranking the whole backlog."""
        if limit <= 0 or max_pages_domain <= 0:
            return []
        now = time.time()
        state = cast(tuple[int] | None, self.conn.execute(
            "SELECT value FROM run_state WHERE key='frontier_scheduler_turn'"
        ).fetchone())
        turn = (state[0] if state else 0) % 4
        chosen: list[CrawlPage] = []
        source_state = cast(tuple[int] | None, self.conn.execute(
            "SELECT value FROM run_state WHERE key='frontier_source_turn'"
        ).fetchone())
        source_turn = (source_state[0] if source_state else 0) % len(
            _DISCOVERY_SOURCE_CLASSES
        )
        with self.conn:
            self.conn.execute("DELETE FROM claimed_batch")
            for _ in range(limit):
                for offset in range(4):
                    lane = (turn + offset) % 4
                    if lane == 0:
                        host_cursor = cast(tuple[str] | None, self.conn.execute(
                            "SELECT last_host FROM frontier_scheduler_cursor WHERE lane=0"
                        ).fetchone())
                        # Complete promising sites before discovering further
                        # low-confidence joined-list domains. These homepages
                        # may exceed the normal internal-page per-host quota.
                        candidate = self._next_scheduled_row(
                            lane, now, source_class="verification-homepage"
                        )
                        if candidate is None and host_cursor is not None and not host_cursor[0]:
                            candidate = self._next_scheduled_row(lane, now)
                        if candidate is None:
                            for source_offset in range(len(_DISCOVERY_SOURCE_CLASSES)):
                                group = _DISCOVERY_SOURCE_CLASSES[
                                    (source_turn + source_offset)
                                    % len(_DISCOVERY_SOURCE_CLASSES)
                                ]
                                candidate = self._next_scheduled_row(
                                    lane, now, source_class=group
                                )
                                if candidate is not None:
                                    break
                    else:
                        candidate = self._next_scheduled_row(lane, now)
                    if candidate is not None:
                        break
                else:
                    break
                url, host, depth, source, kind, due = candidate
                chosen.append(CrawlPage(url, depth, source, kind))
                self.conn.execute(
                    "INSERT OR IGNORE INTO claimed_batch(host) VALUES(?)", (host,)
                )
                self._claimed_per_host[host] += 1
                if self._claimed_per_host[host] >= max_pages_domain:
                    self.conn.execute(
                        "INSERT OR IGNORE INTO exhausted_hosts(host) VALUES(?)", (host,)
                    )
                # Updating the frontier also updates its indexed host heads.
                self.conn.execute(
                    "UPDATE frontier SET status='inflight' WHERE url=?", (url,)
                )
                self.conn.execute(
                    "UPDATE frontier_scheduler_cursor SET last_host=?,last_due=? "
                    + "WHERE lane=?", (host, due, lane),
                )
                if lane == 0:
                    source_class = _scheduler_source_class(source)
                    if source_class in _DISCOVERY_SOURCE_CLASSES:
                        source_turn = (
                            _DISCOVERY_SOURCE_CLASSES.index(source_class) + 1
                        ) % len(_DISCOVERY_SOURCE_CLASSES)
                turn = (lane + 1) % 4
            self.conn.execute(
                "INSERT INTO run_state(key,value) VALUES('frontier_scheduler_turn',?) "
                + "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (turn,),
            )
            self.conn.execute(
                "INSERT INTO run_state(key,value) VALUES('frontier_source_turn',?) "
                + "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (source_turn,),
            )
        return chosen

    def complete(self, page: CrawlPage, error: str = "") -> None:
        if error == "budget-exhausted":
            with self.conn:
                self.conn.execute(
                    "UPDATE frontier SET status='pending',error='' WHERE url=?",
                    (page.url,),
                )
            return
        now = time.time()
        delay = (30 * 86400 if not error or error in
                 {"access denied", "robots denied or unavailable",
                  "robots-access-denied"} else 86400)
        with self.conn:
            self.conn.execute(
                ("UPDATE frontier SET status=?,error=?,last_checked=?,"
                 + "next_check=?,attempts=attempts+1 WHERE url=?"),
                ("failed" if error else "done", error[:240], now,
                 now + delay, page.url),
            )
            if not error:
                self.conn.execute(
                    "UPDATE domains SET last_verified=? WHERE host=?",
                    (now, urlsplit(page.url).hostname or ""),
                )

    def records(self) -> list[Evidence]:
        rows = cast(list[tuple[str]], self.conn.execute(
            "SELECT payload FROM evidence WHERE last_seen >= ? ORDER BY host,category",
            (time.time() - 180 * 86400,)).fetchall())
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

    def set_joined_fingerprint(self, fingerprint: str) -> None:
        # Input list order can change between GitHub Actions runs.
        previous = cast(tuple[str] | None, self.conn.execute(
            "SELECT digest FROM input_fingerprints WHERE key='joined'"
        ).fetchone())
        if previous is None or previous[0] != fingerprint:
            with self.conn:
                self.set_joined_cursor(0)
                self.conn.execute(
                    ("INSERT INTO input_fingerprints(key,digest) VALUES('joined',?) "
                     + "ON CONFLICT(key) DO UPDATE SET digest=excluded.digest"),
                    (fingerprint,),
                )

    def joined_cursor(self) -> int:
        row = cast(tuple[int] | None, self.conn.execute(
            "SELECT value FROM run_state WHERE key='joined_cursor'"
        ).fetchone())
        return row[0] if row else 0

    def set_joined_cursor(self, value: int) -> None:
        with self.conn:
            self.conn.execute(
                ("INSERT INTO run_state(key,value) VALUES('joined_cursor',?) "
                 + "ON CONFLICT(key) DO UPDATE SET value=excluded.value"),
                (value,),
            )

    def search_cursor(self) -> int:
        row = cast(tuple[int] | None, self.conn.execute(
            "SELECT value FROM run_state WHERE key='search_cursor'"
        ).fetchone())
        return int(row[0]) if row else 0

    def advance_search_cursor(self, count: int) -> None:
        with self.conn:
            self.conn.execute(
                ("INSERT INTO run_state(key,value) VALUES('search_cursor',?) "
                 + "ON CONFLICT(key) DO UPDATE SET value=value+excluded.value"),
                (count,),
            )

    def seen_hosts(self) -> set[str]:
        return {row[0] for row in cast(
            list[tuple[str]],
            self.conn.execute("SELECT DISTINCT host FROM frontier").fetchall()
        )}

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
    content_verification: ContentVerificationResult | None = None
    redirect_target: str | None = None


class WebCrawler:
    def __init__(self, config: CrawlConfig,
                 fetch: Callable[..., PageResponse] = safe_get) -> None:
        config.validate()
        self.config: CrawlConfig = config
        self.frontier: Frontier = Frontier(config.state_path)
        self.budget: RequestBudget = RequestBudget(config.max_requests, config.max_seconds)
        self.fetch: Callable[..., PageResponse] = fetch
        self._robots: dict[str, RobotFileParser | None] = {}
        self._robots_missing: set[str] = set()
        self._robots_failures: dict[str, str] = {}
        self._robots_lock: threading.Lock = threading.Lock()
        self._robots_guards: dict[str, threading.Lock] = {}
        self._last_host: dict[str, float] = {}
        self._host_delay: dict[str, float] = {}
        self._host_lock: threading.Lock = threading.Lock()
        self.retries: int = 0
        self.errors: Counter[str] = Counter()
        self.browser_rendered: int = 0
        self.started: float = time.monotonic()
        self.run_id: str = uuid.uuid4().hex
        self._run_host_sources: dict[str, set[str]] = {}
        self._run_attempted_hosts: set[str] = set()
        self._run_homepage_fetched: set[str] = set()
        self._run_attempted_homepages: set[str] = set()
        self._run_homepage_matched: set[str] = set()
        self._run_content_verified: set[str] = set()
        self._homepage_categories: dict[str, tuple[str, ...]] = {}
        self._run_outcomes: Counter[str] = Counter()
        self._http_lock: threading.Lock = threading.Lock()
        report_path = config.report_path or config.state_path.parent / "crawl-report.sqlite"
        if report_path.resolve() == config.state_path.resolve():
            raise ValueError("run report and crawler state paths must differ")
        self.report: RunReport = RunReport(report_path, self.run_id)
        self._progress_last: float = self.started
        self._processed_pages: int = 0
        self._http_status: Counter[str] = Counter()
        self._http_errors: Counter[str] = Counter()
        self._http_latency: Counter[str] = Counter()

    def close(self) -> None:
        self.report.close()
        self.frontier.close()

    def seed(self, records: list[Evidence], *, directories: tuple[str, ...] = (),
             with_public_indexes: bool = True,
             skip_hosts: Collection[str] = ()) -> None:
        for url in ((PUBLIC_INDEXES if with_public_indexes else ()) + directories):
            self.frontier.add(url, depth=0, source="public-index",
                              kind="directory", priority=100)
        families: dict[str, set[str]] = {}
        for entry in records:
            if safe_hostname(entry.hostname) and entry.hostname not in skip_hosts:
                families.setdefault(entry.hostname, set()).add(entry.family)
        # Previous runs should not monopolize a small per-run seed allowance.
        if self.config.max_seed_hosts == 0:
            return
        seen = self.frontier.seen_hosts()
        candidates = sorted(families, key=lambda host: (
            host in seen,
            -len(families[host] - {"derived", "web-lead"}),
            host,
        ))
        selected = 0
        for host in candidates:
            if host in seen:
                continue  # A previously queued URL retains its own retry TTL.
            if self.frontier.add(
                "https://" + host + "/", depth=0, source="catalog",
                priority=45 if len(families[host] - {"derived", "web-lead"}) >= 2 else 35,
            ):
                selected += 1
            if selected >= self.config.max_seed_hosts:
                break

    def seed_joined(self, hosts: Iterable[str], *,
                    skip_hosts: Collection[str] = (),
                    fingerprint: str | None = None) -> None:
        """Incrementally inspect untrusted community domains over many runs.

        Joined-list evidence never counts as an independent catalog or
        authorizes publication. The cursor advances only past examined rows.
        """
        if self.config.max_joined_hosts == 0:
            return
        if fingerprint is not None:
            self.frontier.set_joined_fingerprint(fingerprint)
        offset = self.frontier.joined_cursor()
        previously_seen = self.frontier.seen_hosts()
        selected = 0
        consumed = offset
        exhausted = True
        for i, hostname in enumerate(hosts):
            if i < offset:
                continue
            consumed = i + 1
            host = normalize_hostname(hostname)
            if (host and host not in skip_hosts and host not in previously_seen
                    and safe_hostname(host)):
                if self.frontier.add("https://" + host + "/", depth=0,
                                     source="joined-candidate", priority=10):
                    self.frontier.evidence(Evidence(
                        host, None, "unknown", (), "joined-candidate", None,
                        "web-lead", "domain", origin="newedgex-joined",
                    ))
                    selected += 1
            if selected >= self.config.max_joined_hosts:
                exhausted = False
                break
        self.frontier.set_joined_cursor(0 if exhausted else consumed)
        logger.info("joined backlog: queued %d unverified hosts", selected)

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
            stage = ("robots" if urlsplit(url).path == "/robots.txt" else
                     "sitemap" if urlsplit(url).path == "/sitemap.xml" else
                     "search" if host == "api.search.brave.com" else "page")
            started = time.monotonic()
            try:
                response = self.fetch(url, self.budget, timeout=self.config.timeout,
                                      max_bytes=max_bytes, headers=headers)
            except Exception as error:
                duration = time.monotonic() - started
                with self._http_lock:
                    self._http_errors[stage + ":" + type(error).__name__] += 1
                    self._http_latency["under_1s" if duration < 1 else
                                       "under_5s" if duration < 5 else "over_5s"] += 1
                self.report.record_http(
                    host, stage, error_code=type(error).__name__,
                    latency_ms=max(0, int(duration * 1000)), attempt=attempt + 1,
                )
                if isinstance(error, (TimeoutError, ConnectionError)):
                    if attempt == 0 and self.budget.remaining():
                        self.retries += 1
                        time.sleep(0.5)
                        continue
                raise
            duration = time.monotonic() - started
            with self._http_lock:
                self._http_status[stage + ":" + str(response.status)] += 1
                self._http_latency["under_1s" if duration < 1 else
                                   "under_5s" if duration < 5 else "over_5s"] += 1
            self.report.record_http(
                host, stage, status_code=response.status,
                latency_ms=max(0, int(duration * 1000)), attempt=attempt + 1,
            )
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
        for lang, query in search_queries(
            max_queries=self.config.max_search_queries,
            start_index=self.frontier.search_cursor(), templates=templates,
        ):
            if not self.budget.remaining():
                break
            self.frontier.advance_search_cursor(1)
            url = ("https://api.search.brave.com/res/v1/web/search?q="
                   + quote(query, safe="") + "&count=20")
            try:
                result = self._request(url, max_bytes=250_000,
                                       headers={"X-Subscription-Token": token, "Accept": "application/json"})
                if result.status != 200:
                    self.errors[f"search-http-{result.status}"] += 1
                    if result.status in (401, 403, 429):
                        break  # Do not evade authentication or API rate limits.
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
        # Prevent sensitive probes even with an absent or permissive robots.
        if not public_crawl_url(url):
            return False
        host = urlsplit(url).hostname
        if host is None:
            return False
        with self._robots_lock:
            guard = self._robots_guards.setdefault(host, threading.Lock())
        with guard:
            if host in self._robots_failures:
                return False
            if host not in self._robots:
                robots_url = "https://" + host + "/robots.txt"
                try:
                    response = self._request(robots_url, max_bytes=MAX_ROBOTS)
                    if response.status in (401, 403):
                        self._robots_failures[host] = "robots-access-denied"
                        return False
                    if response.status == 429:
                        self._robots_failures[host] = "robots-rate-limited"
                        return False
                    if response.status >= 500:
                        self._robots_failures[host] = "robots-temporary-error"
                        return False
                    parser: RobotFileParser | None = None
                    # Some websites return a branded HTML "404" with HTTP
                    # 200 for missing files. Only an explicit not-found title
                    # permits fallback; an HTML challenge/login remains denied.
                    response_start = response.body[:512].lower()
                    html_response = ("html" in response.content_type.lower()
                                     or b"<html" in response_start
                                     or b"<!doctype html" in response_start)
                    soft_missing = (
                        response.status == 200
                        and html_response
                        and b"user-agent:" not in response.body.lower()
                        and missing_robots_html(response.body)
                    )
                    if response.status in (404, 410) or soft_missing:
                        self._robots_missing.add(host)
                        logger.debug(
                            "robots absent: host=%s status=%d; public HTML paths only",
                            host, response.status,
                        )
                    elif response.status == 200:
                        if ("html" in response.content_type.lower()
                                or b"<html" in response.body[:512].lower()):
                            self._robots_failures[host] = "robots-invalid-content"
                            return False
                        parser = RobotFileParser()
                        parser.parse(response.body.decode("utf-8", "replace").splitlines())
                        delay = parser.crawl_delay(AGENT)
                        rate = parser.request_rate(AGENT)
                        paced_delay = self.config.delay
                        if delay is not None:
                            paced_delay = max(paced_delay, float(delay))
                        if rate is not None:
                            if rate.requests <= 0:
                                self._robots_failures[host] = "robots-access-denied"
                                return False
                            paced_delay = max(paced_delay, rate.seconds / rate.requests)
                        if paced_delay > self.config.max_seconds:
                            self._robots_failures[host] = "robots-pacing-exceeds-budget"
                            return False
                        with self._host_lock:
                            self._host_delay[host] = paced_delay
                    else:
                        self._robots_failures[host] = f"robots-http-{response.status}"
                        logger.debug("robots denied: host=%s status=%d", host, response.status)
                        return False
                    self._robots[host] = parser
                except (OSError, ValueError, RuntimeError, http.client.HTTPException,
                        ssl.SSLError) as error:
                    reason = ("robots-tls-error" if isinstance(error, ssl.SSLError)
                              else "robots-dns-error" if isinstance(error, socket.gaierror)
                              else "robots-unavailable")
                    self._robots_failures[host] = reason
                    logger.debug("robots fetch failed: host=%s cause=%s detail=%s",
                                host, reason, type(error).__name__)
                    return False
            robots = self._robots[host]
        return robots is None or robots.can_fetch(AGENT, url)

    def _inspect(self, page: CrawlPage) -> InspectedPage:
        parts = urlsplit(page.url)
        host = parts.hostname or ""
        logger.debug("crawl visiting: host=%s path=%s source=%s depth=%d",
                    host, parts.path or "/", page.source, page.depth)
        if not self._allowed(page.url):
            reason = ("sensitive-url" if not public_crawl_url(page.url)
                      else "budget-exhausted" if not self.budget.remaining()
                      else self._robots_failures.get(host, "robots denied or unavailable"))
            return InspectedPage(page, "", (), (), (), (), reason)
        try:
            effective_url = page.url
            response = self._request(effective_url)
            seen_redirects = {effective_url}
            # Same-origin HTTPS redirects can be checked under the current
            # robots policy. Cross-origin redirects become independent leads
            # and must pass their own robots and DNS guards on the next visit.
            for _ in range(3):
                if response.status not in (301, 302, 307, 308):
                    break
                target = normalize_url(urljoin(effective_url, response.location)) if response.location else None
                if not target or not public_crawl_url(target):
                    return InspectedPage(page, "", (), (), (), (), "unsafe-redirect")
                target_host = urlsplit(target).hostname
                if target_host != host:
                    if not target_host or not safe_hostname(target_host):
                        return InspectedPage(page, "", (), (), (), (), "unsafe-redirect")
                    return InspectedPage(
                        page, "", (), (), (), (), "redirect-other-host",
                        redirect_target=f"https://{target_host}/",
                    )
                if target in seen_redirects:
                    return InspectedPage(page, "", (), (), (), (), "redirect-loop")
                if not self._allowed(target):
                    return InspectedPage(page, "", (), (), (), (), "redirect-robots-denied")
                seen_redirects.add(target)
                effective_url = target
                response = self._request(effective_url)
            if response.status in (301, 302, 307, 308):
                return InspectedPage(page, "", (), (), (), (), "redirect-limit")
            if response.status in (401, 403, 429):
                return InspectedPage(page, "", (), (), (), (), "access denied")
            if response.status != 200:
                return InspectedPage(page, "", (), (), (), (), "http-" + str(response.status))
            if "html" not in response.content_type.lower():
                return InspectedPage(page, "", (), (), (), (), "not HTML")
            text = response.body.decode("utf-8", "replace")
            parser = SitePage()
            parser.feed(text)
            verification = (
                verify_content_page(
                    text, effective_url,
                    homepage_categories=self._homepage_categories.get(host, ()),
                    parsed_soup=parser.soup,
                )
                if page.url != f"https://{host}/" and page.kind != "directory"
                else None
            )
            categories = site_categories(parser.topic_text)
            links = ranked_links(
                text, effective_url, allow_generic_external=page.kind == "directory",
                allow_opaque_internal=bool(categories),
                parsed_soup=parser.soup,
            )
            if page.source == "joined-candidate":
                # A broad community blocklist is not reliable evidence on its
                # own. Ignore incidental mentions in a generic blog/body.
                prominent = site_categories(" ".join((
                    parser.title, parser.og_title, parser.description,
                    parser.keywords, *parser.headings, *parser.json_ld,
                )))
                thematic_internal = sum(
                    urlsplit(link).hostname == host and
                    bool(site_categories(unquote(urlsplit(link).path)))
                    for link in links
                )
                if not prominent and thematic_internal < 2:
                    categories = ()
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
                                        if loc and urlsplit(loc).hostname == host and (
                                                site_categories(unquote(urlsplit(loc).path))
                                                or content_page_path(urlsplit(loc).path)):
                                            paths.append(loc)
                    except (OSError, ValueError, RuntimeError, ET.ParseError, http.client.HTTPException):
                        pass
            return InspectedPage(page, parser.title, categories, _languages(parser.language),
                                 links, tuple(paths), needs_browser=not categories and page.depth == 0
                                 and page.kind != "directory",
                                 content_verification=verification)
        except (OSError, ValueError, RuntimeError, http.client.HTTPException,
                ssl.SSLError) as error:
            return InspectedPage(page, "", (), (), (), (),
                                 "budget-exhausted" if not self.budget.remaining()
                                 else type(error).__name__)

    def _record(self, data: InspectedPage, browser: BrowserRenderer | None) -> None:
        page = data.page
        host = urlsplit(page.url).hostname or ""
        self._run_attempted_hosts.add(host)
        if page.url == f"https://{host}/":
            self._run_attempted_homepages.add(host)
        self._run_host_sources.setdefault(host, set()).add(source_group(page.source))
        page_type = ("directory" if page.kind == "directory" else
                     "homepage" if page.url == f"https://{host}/" else "content")
        self._processed_pages += 1
        now = time.monotonic()
        if self._processed_pages % 100 == 0 or now - self._progress_last >= 60:
            self._progress_last = now
            logger.info(
                "crawl progress: pages=%d hosts=%d topical=%d requests=%d/%d errors=%d",
                self._processed_pages, len(self._run_attempted_hosts),
                len(self._run_homepage_matched),
                self.budget.requests, self.config.max_requests,
                sum(self.errors.values()),
            )
        if data.error:
            if data.redirect_target and page.depth < self.config.max_depth:
                target = normalize_url(data.redirect_target)
                if target and public_crawl_url(target):
                    self.frontier.add(
                        target, depth=page.depth + 1,
                        source="redirect:" + host, priority=85,
                    )
            self._run_outcomes[data.error] += 1
            self.report.record_page(
                host, source_group(page.source), page_type, data.error,
            )
            logger.debug("crawl skipped: host=%s path=%s reason=%s",
                        host, urlsplit(page.url).path or "/", data.error)
            if data.error != "budget-exhausted":
                if page.url == "https://" + host + "/":
                    self.frontier.clear_homepage_evidence(host)
                else:
                    self.frontier.clear_page_evidence(host, page.url)
            self.frontier.complete(page, data.error)
            self.errors[data.error] += 1
            return
        if page.url == "https://" + host + "/":
            self.frontier.clear_homepage_evidence(host)
        else:
            self.frontier.clear_page_evidence(host, page.url)
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
        is_homepage = page.url == "https://" + host + "/"
        if is_homepage:
            self._run_homepage_fetched.add(host)
            if categories:
                self._run_homepage_matched.add(host)
                self._homepage_categories[host] = categories
            else:
                self._homepage_categories.pop(host, None)
        verification = data.content_verification
        structured_content = bool(
            not is_homepage and page.kind != "directory"
            and verification is not None and verification.verified
        )
        if structured_content and verification is not None:
            title = verification.title or title
            categories = verification.categories
            method = "verified-content-page"
            self._run_content_verified.add(host)
        elif not is_homepage:
            # Incidental topical titles or generic HTML routes cannot create
            # site-internal-page evidence or trigger outbound discovery.
            categories = ()
        outcome = ("homepage_topical" if is_homepage and categories else
                   "homepage_unverified" if is_homepage else
                   "content_verified" if structured_content else "content_checked")
        self._run_outcomes[outcome] += 1
        def signal_codes(values: tuple[str, ...]) -> tuple[str, ...]:
            return tuple("CONTENT_" + code.upper() + "_V1" for code in values)
        self.report.record_page(
            host, source_group(page.source), page_type, outcome,
            reason_codes=signal_codes(verification.codes) if verification else (),
            positive_signals=(
                signal_codes(verification.positive_signals) if verification else
                ("HOMEPAGE_TOPIC_MATCH_V1",) if is_homepage and categories else ()
            ),
            negative_signals=(
                signal_codes(verification.negative_signals) if verification else
                ("HOMEPAGE_NO_TOPIC_V1",) if is_homepage else ()
            ),
        )
        if page.kind != "directory" and safe_hostname(host) and (
                (is_homepage and categories) or structured_content):
            if categories:
                logger.debug("crawl matched: host=%s categories=%s method=%s",
                            host, ",".join(categories), method)
            # A content route is evidence only after structural verification.
            repository = "site-homepage" if is_homepage else "site-internal-page"
            for category in categories or ("unknown",):
                self.frontier.evidence(Evidence(
                    host, title or None, category, languages, repository, None,
                    "web-lead", method, origin=page.url,
                ))
        if structured_content and host not in self._run_attempted_homepages:
            self.frontier.prioritize_homepage(host)
        for loc in data.sitemap_paths:
            self.frontier.add(loc, depth=page.depth + 1,
                              source="sitemap:" + host, priority=25)
        if page.depth >= self.config.max_depth or not (categories or page.kind == "directory"):
            self.frontier.complete(page)
            return
        for href in data.links:
            url = normalize_url(urljoin(page.url, href))
            if not url:
                continue
            target = urlsplit(url).hostname
            if target is None:
                continue
            if target == host:
                if (site_categories(unquote(urlsplit(url).path))
                        or content_page_path(urlsplit(url).path)
                        or (categories and url in data.links)):
                    self.frontier.add(url, depth=page.depth + 1, source="internal:" + host,
                                      priority=75)
            elif safe_hostname(target):
                # Every external link remains a lead pending target verification.
                self.frontier.add("https://" + target + "/", depth=page.depth + 1,
                                  source="outbound:" + host, priority=55)
                self.frontier.evidence(Evidence(target, None, "manga", (), "web-link", None,
                                               "web-lead", "href", origin=page.url))
        # A page is done only after discovered links and verification evidence
        # are durable. Interrupted processing is retried on next startup.
        self.frontier.complete(page)

    def run(self) -> dict[str, object]:
        browser = BrowserRenderer(max_sites=self.config.max_browser) if self.config.max_browser else None
        completion_queued = self.frontier.prioritize_promising_homepages()
        if completion_queued:
            logger.info("crawl completion: prioritized %d missing homepages", completion_queued)
        logger.info("crawl started: workers=%d request_limit=%d time_limit=%.0fs",
                    self.config.concurrency, self.config.max_requests, self.config.max_seconds)
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
        run_funnel = {
            "queued_hosts": len(self.frontier.queued_hosts),
            "attempted_hosts": len(self._run_attempted_hosts),
            "html_homepage_hosts": len(self._run_homepage_fetched),
            "topical_homepage_hosts": len(self._run_homepage_matched),
            "content_page_hosts_verified": len(self._run_content_verified),
        }
        current_run: dict[str, object] = {
            "run_id": self.run_id,
            "funnel": run_funnel,
            "requests": self.budget.requests,
            "request_budget": self.config.max_requests,
            "duration_seconds": round(time.monotonic() - self.started, 2),
            "http_statuses": dict(sorted(self._http_status.items())),
            "http_errors": dict(sorted(self._http_errors.items())),
            "http_latency_buckets": dict(sorted(self._http_latency.items())),
            "page_outcomes": dict(sorted(self._run_outcomes.items())),
            "queued_urls_by_source": dict(sorted(self.frontier.queued_this_run.items())),
            "enqueue_rejections": dict(sorted(self.frontier.enqueue_rejections.items())),
            "robots_missing_hosts": len(self._robots_missing),
            "robots_errors": dict(sorted(Counter(self._robots_failures.values()).items())),
        }
        self.report.put_metadata("current_run", current_run)
        self.report.put_metadata("lifetime_state", {
            key: stats[key] for key in (
                "urls_discovered", "urls_crawled", "urls_failed",
                "urls_pending", "unique_domains", "evidence_domains",
            ) if key in stats
        })
        self.report.put_metadata("status", "crawl_complete")
        stats.update({
            "current_run": current_run,
            "run_report_path": str(self.report.path),
            "run_host_sources": {host: sorted(groups) for host, groups in
                                 sorted(self._run_host_sources.items())},
            "requests": self.budget.requests,
            "request_budget": self.config.max_requests,
            "duration_seconds": round(time.monotonic() - self.started, 2),
            "browser_rendered": self.browser_rendered,
            "retry_count": self.retries,
            "robots_missing_hosts": len(self._robots_missing),
            "errors": dict(self.errors),
            "websites_verified": len({e.hostname for e in records if e.repository == "site-homepage"}),
            "language_distribution": dict(Counter(lang for e in records for lang in e.languages)),
        })
        logger.info(
            "crawl finished: pages=%d hosts=%d topical=%d failures=%d "
            + "requests=%d/%d duration=%.1fs",
            self._processed_pages, len(self._run_attempted_hosts),
            len(self._run_homepage_matched), sum(self.errors.values()),
            self.budget.requests, self.config.max_requests,
            time.monotonic() - self.started,
        )
        return stats


def fingerprint_file(path: Path) -> str:
    """Stream a SHA-256 of the current upstream list, without loading it."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def crawl_web(records: list[Evidence], config: CrawlConfig, *,
              directories: tuple[str, ...] = (), public_indexes: bool = True,
              search_token: str | None = None,
              search_templates: dict[str, str] | None = None,
              joined_hosts: Iterable[str] = (),
              joined_fingerprint: str | None = None,
              skip_hosts: Collection[str] = (),
              ) -> tuple[list[Evidence], dict[str, object]]:
    crawler = WebCrawler(config)
    try:
        crawler.seed(records, directories=directories, with_public_indexes=public_indexes,
                     skip_hosts=skip_hosts)
        crawler.seed_joined(joined_hosts, skip_hosts=skip_hosts,
                            fingerprint=joined_fingerprint)
        crawler.search(search_token, templates=search_templates)
        stats = crawler.run()
        return crawler.frontier.records(), stats
    finally:
        crawler.close()
