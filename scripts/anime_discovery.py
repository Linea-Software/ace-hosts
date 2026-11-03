"""Evidence-led anime/manga discovery and bounded public-site scraping.

Catalogs provide candidate homepages; opted-in HTML scraping inspects public
site metadata and outbound leads, subject to robots.txt. Neither a scraped
page nor an unverified web link is an independent catalog confirmation.
"""
from __future__ import annotations

import argparse
import html.parser
import ipaddress
import json
import logging
import re
import socket
import sys
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol, cast
from urllib.parse import quote, unquote, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import requests

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import utils
from scripts.anime_browser import BrowserRenderer

logger = logging.getLogger("ace-hosts.anime-discovery")
MAX_RESPONSE_BYTES = 10_000_000
MAX_CANDIDATES = 50_000
MIN_JOINED_DOMAINS = 25_000
MAX_DIRECTORY_LINKS = 400
MAX_HTML_BYTES = 1_000_000
MAX_SITEMAP_URLS = 400
MIN_INDEPENDENT_FAMILIES = 2
CATEGORY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("anime", re.compile(r"anime|アニメ|动漫", re.IGNORECASE)),
    ("manga", re.compile(r"manga|漫画|マンガ|mangá|манга|komik|truyện tranh|มังงะ", re.IGNORECASE)),
    ("manhwa", re.compile(r"manhwa|만화", re.IGNORECASE)),
    ("manhua", re.compile(r"manhua|国漫", re.IGNORECASE)),
    ("webtoon", re.compile(r"webtoon|웹툰", re.IGNORECASE)),
    ("scanlation", re.compile(r"scanlation", re.IGNORECASE)),
    ("light-novel", re.compile(r"light.?novel|ライトノベル", re.IGNORECASE)),
    ("webnovel", re.compile(r"web.?novel|网络小说", re.IGNORECASE)),
)
# Exact general services plus shared infrastructure: a thematic *subdomain*
# is preserved if its parent is not shared, never collapsed to an eTLD+1.
SHARED_SUFFIXES = frozenset({
    "github.com", "github.io", "gitlab.com", "reddit.com", "facebook.com",
    "instagram.com", "twitter.com", "x.com", "amazon.com", "amazonaws.com",
    "google.com", "googleapis.com", "googleusercontent.com", "youtube.com",
    "tiktok.com", "discord.com", "telegram.org", "cloudfront.net",
    "cloudflare.com", "cloudflare.net", "workers.dev", "pages.dev",
    "vercel.app", "netlify.app", "azurewebsites.net", "herokuapp.com",
    "firebaseapp.com", "firebaseio.com", "githubusercontent.com",
    "jsdelivr.net", "npmjs.com", "microsoft.com", "apple.com",
    "paypal.com", "stripe.com", "wordpress.com", "blogspot.com",
    "wikipedia.org", "archive.org", "googlevideo.com", "akamaihd.net",
    "fastly.net", "githubassets.com", "localhost",
})


@dataclass(frozen=True)
class Catalog:
    name: str
    url: str
    adapter: str
    family: str
    license: str


# A catalog mirrored or derived from another is NOT an independent vote.
CATALOGS: tuple[Catalog, ...] = (
    Catalog("keiyoushi", "https://raw.githubusercontent.com/keiyoushi/extensions/repo/index.min.json",
            "tachiyomi", "mihon", "Apache-2.0"),
    Catalog("aniyomi", "https://raw.githubusercontent.com/aniyomiorg/aniyomi-extensions/repo/index.min.json",
            "tachiyomi", "aniyomi", "Apache-2.0"),
    Catalog("mangayomi", "https://raw.githubusercontent.com/kodjodevf/mangayomi-extensions/main/index.json",
            "mangayomi", "mangayomi", "Apache-2.0; archived"),
    Catalog("mangayomi-novel", "https://raw.githubusercontent.com/kodjodevf/mangayomi-extensions/main/novel_index.json",
            "mangayomi", "mangayomi", "Apache-2.0; archived"),
    Catalog("aidoku", "https://raw.githubusercontent.com/Aidoku-Community/sources/gh-pages/index.min.json",
            "aidoku", "aidoku", "MIT OR Apache-2.0"),
    Catalog("miru", "https://raw.githubusercontent.com/miru-project/repo/main/index.json",
            "miru", "miru", "MIT"),
    Catalog("lnreader", "https://raw.githubusercontent.com/LNReader/lnreader-plugins/plugins/v3.0.0/.dist/plugins.min.json",
            "lnreader", "lnreader", "see upstream MIT license"),
    Catalog("kuiti", "https://raw.githubusercontent.com/kuiti/manga-source-index/main/01-all-sources/00-deduplicated-master.json",
            "kuiti", "derived", "MIT compilation; inherited upstream terms"),
)

SUPPORTED_LANGUAGES = frozenset({
    "en", "it", "ja", "ko", "zh", "id", "vi", "th", "es", "pt", "fr",
    "de", "ru", "tr", "ar",
})
CONTENT_TYPES = frozenset({
    "anime", "manga", "manhwa", "manhua", "webtoon", "light-novel",
    "webnovel", "scanlation",
})


def normalize_hostname(value: object) -> str | None:
    """Normalize an explicit HTTPS/HTTP site URL or bare hostname, never paths."""
    if not isinstance(value, str) or not value or len(value) > 2048:
        return None
    value = value.strip()
    if any(char.isspace() for char in value) or value.startswith(("//", "*", "!")):
        return None
    try:
        parsed = urlsplit(value if "://" in value else "https://" + value)
        if parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
            return None
        if parsed.port is not None and parsed.port not in (80, 443):
            return None
        raw = parsed.hostname
        if not raw or raw.endswith(".."):
            return None
        host = raw.rstrip(".").encode("idna").decode("ascii").lower()
        if len(host) > 253 or not utils.is_valid_domain(host) or "." not in host:
            return None
        if host.endswith((".local", ".localhost", ".invalid", ".test", ".example", ".internal")):
            return None
        try:
            ipaddress.ip_address(host)
            return None
        except ValueError:
            return host
    except (ValueError, UnicodeError):
        return None


def is_shared(host: str) -> bool:
    return any(host == suffix or host.endswith("." + suffix) for suffix in SHARED_SUFFIXES)


def safe_hostname(host: str) -> bool:
    """Exclude known infrastructure without converting a child to its parent."""
    return normalize_hostname(host) == host and not is_shared(host)


@dataclass(frozen=True)
class Evidence:
    hostname: str
    name: str | None
    category: str
    languages: tuple[str, ...]
    repository: str
    extension_id: str | None
    family: str
    field: str
    license: str | None = None


def _languages(value: object) -> tuple[str, ...]:
    raw: list[object] = cast(list[object], value) if isinstance(value, list) else [value]
    languages: set[str] = set()
    aliases = {"english": "en", "italiano": "it", "japanese": "ja", "korean": "ko",
               "chinese": "zh", "indonesian": "id", "vietnamese": "vi",
               "thai": "th", "spanish": "es", "portuguese": "pt",
               "french": "fr", "german": "de", "russian": "ru",
               "turkish": "tr", "arabic": "ar", "العربية": "ar"}
    for item in raw:
        if not isinstance(item, str):
            continue
        normalized = item.strip().replace("\u200e", "").lower().replace("_", "-").split("-", 1)[0]
        normalized = aliases.get(normalized, normalized)
        if normalized in SUPPORTED_LANGUAGES:
            languages.add(normalized)
    return tuple(sorted(languages))


def _category(value: object, default: str) -> str:
    kind = str(value).lower().strip() if value is not None else default
    return {
        "0": "manga", "1": "anime", "2": "webnovel",
        "bangumi": "anime", "comic": "manga", "novel": "webnovel",
        "video": "anime", "anime": "anime", "manga": "manga",
        "webtoon": "webtoon", "manhua": "manhua", "manhwa": "manhwa",
        "light-novel": "light-novel", "webnovel": "webnovel",
    }.get(kind, default)


def _evidence(item: dict[str, object], catalog: Catalog, *, url_key: str,
              lang_key: str, id_key: str, category: str) -> Evidence | None:
    url = item.get(url_key)
    host = normalize_hostname(url)
    if host is None:
        return None
    name = item.get("name")
    identifier = item.get(id_key)
    return Evidence(
        hostname=host,
        name=name.strip() if isinstance(name, str) and name.strip() else None,
        category=category,
        languages=_languages(item.get(lang_key)),
        repository=catalog.name,
        extension_id=str(identifier) if isinstance(identifier, (str, int)) else None,
        family=catalog.family,
        field=url_key,
        license=catalog.license,
    )


def _parse_tachiyomi(item: dict[str, object], catalog: Catalog) -> list[Evidence]:
    """Parse a Mihon/Aniyomi extension's declared source home URLs."""
    entries = item.get("sources")
    if not isinstance(entries, list):
        return []
    results: list[Evidence] = []
    for raw_source in cast(list[object], entries[:1000]):
        if not isinstance(raw_source, dict):
            continue
        source = cast(dict[str, object], raw_source)
        evidence = _evidence(source, catalog, url_key="baseUrl", lang_key="lang",
                             id_key="id",
                             category="anime" if catalog.name == "aniyomi" else "manga")
        if evidence is not None:
            results.append(evidence)
    return results


def _parse_mangayomi(item: dict[str, object], catalog: Catalog) -> list[Evidence]:
    evidence = _evidence(item, catalog, url_key="baseUrl", lang_key="lang",
                         id_key="id", category=_category(item.get("itemType"), "manga"))
    return [evidence] if evidence is not None else []


def _parse_aidoku(item: dict[str, object], catalog: Catalog) -> list[Evidence]:
    evidence = _evidence(item, catalog, url_key="baseURL", lang_key="languages",
                         id_key="id", category="manga")
    return [evidence] if evidence is not None else []


def _parse_miru(item: dict[str, object], catalog: Catalog) -> list[Evidence]:
    evidence = _evidence(item, catalog, url_key="webSite", lang_key="lang",
                         id_key="package", category=_category(item.get("type"), "anime"))
    return [evidence] if evidence is not None else []


def _parse_lnreader(item: dict[str, object], catalog: Catalog) -> list[Evidence]:
    evidence = _evidence(item, catalog, url_key="site", lang_key="lang",
                         id_key="id", category="webnovel")
    return [evidence] if evidence is not None else []


def _parse_kuiti(item: dict[str, object], catalog: Catalog) -> list[Evidence]:
    """Derived dataset: useful leads, *not* a new independent family."""
    enriched: dict[str, object] = dict(item)
    if "name" not in enriched:
        names = enriched.get("names")
        if isinstance(names, str):
            enriched["name"] = names.split("|", 1)[0]
    for key in ("domain", "homeUrl", "url"):
        if key in enriched:
            evidence = _evidence(enriched, catalog, url_key=key, lang_key="langs",
                                 id_key="id", category="manga")
            return [evidence] if evidence is not None else []
    return []


ADAPTERS: dict[str, Callable[[dict[str, object], Catalog], list[Evidence]]] = {
    "tachiyomi": _parse_tachiyomi,
    "mangayomi": _parse_mangayomi,
    "aidoku": _parse_aidoku,
    "miru": _parse_miru,
    "lnreader": _parse_lnreader,
    "kuiti": _parse_kuiti,
}


def parse_catalog(payload: object, catalog: Catalog) -> list[Evidence]:
    """Parse only declared site URLs; each adapter is independent and testable."""
    adapter = ADAPTERS.get(catalog.adapter)
    if adapter is None:
        raise ValueError(f"unknown adapter: {catalog.adapter}")
    if catalog.adapter == "aidoku":
        if not isinstance(payload, dict):
            raise ValueError("Aidoku catalog is not an object")
        records = cast(dict[str, object], payload).get("sources")
    elif catalog.adapter == "kuiti" and isinstance(payload, dict):
        item = cast(dict[str, object], payload)
        records = item.get("sources", item.get("sites"))
    else:
        records = payload
    if not isinstance(records, list):
        raise ValueError(f"{catalog.name}: invalid catalog structure")
    typed_records = cast(list[object], records)
    if len(typed_records) > MAX_CANDIDATES:
        raise ValueError(f"{catalog.name}: invalid catalog size")
    results: list[Evidence] = []
    for raw_item in typed_records:
        if isinstance(raw_item, dict):
            results.extend(adapter(cast(dict[str, object], raw_item), catalog))
    return results


class DirectoryLinks(html.parser.HTMLParser):
    """One-page directory extraction; no recursive crawling."""
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:  # pyright: ignore[reportImplicitOverride]
        if tag == "a" and len(self.links) < MAX_DIRECTORY_LINKS:
            address = dict(attrs).get("href")
            if address:
                self.links.append(address)


def _fetch(session: requests.Session, url: str, *, timeout: float, attempts: int = 3,
           max_bytes: int = MAX_RESPONSE_BYTES, content_type: str | None = None) -> bytes:
    """Bound downloaded bytes, retries and timeouts; do not follow redirects to HTTP."""
    if not url.startswith("https://"):
        raise ValueError("only HTTPS is permitted")
    for attempt in range(attempts):
        try:
            with session.get(url, timeout=timeout, stream=True, allow_redirects=False) as response:
                response.raise_for_status()
                if 300 <= response.status_code < 400:
                    raise ValueError("redirects are not allowed in discovery")
                if content_type is not None and content_type not in response.headers.get(
                    "Content-Type", ""
                ).lower():
                    raise ValueError("unexpected content type for scraped page")
                size = 0
                body = bytearray()
                for chunk in response.iter_content(64 * 1024):
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError("response exceeds discovery byte limit")
                    body.extend(chunk)
                return bytes(body)
        except (requests.RequestException, ValueError):
            if attempt == attempts - 1:
                raise
            time.sleep((1, 2, 4)[min(attempt, 2)])
    raise AssertionError("unreachable")


def fetch_catalog(session: requests.Session, catalog: Catalog, cache_dir: Path,
                  *, timeout: float = 12, refresh: bool = False) -> list[Evidence]:
    """Bounded local cache; a failed refresh never silently reuses stale content."""
    path = cache_dir / f"{catalog.name}.json"
    if path.is_file() and not refresh:
        raw = path.read_bytes()
    else:
        raw = _fetch(session, catalog.url, timeout=timeout)
    payload = cast(object, json.loads(raw))
    records = parse_catalog(payload, catalog)
    if not records:
        raise ValueError(f"{catalog.name}: no declared site URLs found")
    if refresh or not path.is_file():
        cache_dir.mkdir(parents=True, exist_ok=True)
        utils.atomic_write_lines(path, [raw.decode("utf-8")])
    return records


def fetch_directory(session: requests.Session, url: str, *, timeout: float = 12) -> list[Evidence]:
    """Opt-in single-page fetch obeying robots; search hits are weak leads only."""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password:
        raise ValueError("directory must have a public HTTPS URL")
    host = normalize_hostname(url)
    if host is None or is_shared(host):
        raise ValueError("directory must use a dedicated public hostname")
    robots = RobotFileParser()
    robots.set_url(f"https://{parsed.netloc}/robots.txt")
    robots.parse(_fetch(session, f"https://{parsed.netloc}/robots.txt",
                        timeout=timeout).decode("utf-8", errors="replace").splitlines())
    if not robots.can_fetch("ace-hosts/0.1", url):
        raise ValueError("directory robots policy disallows discovery")
    page = _fetch(session, url, timeout=timeout)
    parser = DirectoryLinks()
    parser.feed(page.decode("utf-8", errors="replace"))
    records: list[Evidence] = []
    for href in parser.links:
        candidate = normalize_hostname(urljoin(url, href))
        if candidate and candidate != host and safe_hostname(candidate):
            records.append(Evidence(candidate, None, "manga", (), "web-directory",
                                    None, "web-lead", "href"))
    return records


class SitePage(DirectoryLinks):
    """Bounded visible metadata: title, OpenGraph, headings and JSON-LD."""

    def __init__(self) -> None:
        super().__init__()
        self.title_parts: list[str] = []
        self.description: str = ""
        self.keywords: str = ""
        self.og_title: str = ""
        self.language: str = ""
        self.headings: list[str] = []
        self.json_ld: list[str] = []
        self._in_title: bool = False
        self._in_heading: bool = False
        self._in_json_ld: bool = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:  # pyright: ignore[reportImplicitOverride]
        super().handle_starttag(tag, attrs)
        attributes = dict(attrs)
        if tag == "title":
            self._in_title = True
        elif tag in {"h1", "h2"}:
            self._in_heading = True
        elif tag == "script" and (attributes.get("type") or "").lower() == "application/ld+json":
            self._in_json_ld = True
        elif tag == "html":
            self.language = attributes.get("lang") or ""
        elif tag == "meta":
            key = (attributes.get("name") or attributes.get("property") or "").lower()
            content = (attributes.get("content") or "")[:1000]
            if key in {"description", "og:description", "twitter:description"}:
                self.description = content if not self.description else self.description
            elif key == "keywords":
                self.keywords = content
            elif key in {"og:title", "twitter:title"}:
                self.og_title = content

    def handle_endtag(self, tag: str) -> None:  # pyright: ignore[reportImplicitOverride]
        if tag == "title":
            self._in_title = False
        elif tag in {"h1", "h2"}:
            self._in_heading = False
        elif tag == "script":
            self._in_json_ld = False

    def handle_data(self, data: str) -> None:  # pyright: ignore[reportImplicitOverride]
        if self._in_title and sum(map(len, self.title_parts)) < 512:
            self.title_parts.append(data[:512])
        elif self._in_heading and len(self.headings) < 15:
            self.headings.append(data[:200])
        elif self._in_json_ld and len(self.json_ld) < 3:
            # JSON-LD is untrusted text, not code. No JSON-LD fetches are made.
            self.json_ld.append(data[:1000])

    @property
    def title(self) -> str:
        return (" ".join(self.title_parts).strip() or self.og_title)[:200]

    @property
    def topic_text(self) -> str:
        return " ".join((
            self.title, self.og_title, self.description, self.keywords,
            *self.headings, *self.json_ld,
        ))[:6000]


def site_categories(text: str) -> tuple[str, ...]:
    """Infer only explicitly mentioned content categories from page metadata."""
    return tuple(name for name, pattern in CATEGORY_PATTERNS if pattern.search(text))


def _public_dns_host(host: str) -> bool:
    """Refuse homepages resolving to private, loopback or otherwise non-global IPs."""
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        return bool(addresses) and all(
            ipaddress.ip_address(address[4][0]).is_global for address in addresses
        )
    except (OSError, ValueError):
        return False


def fetch_sitemap_evidence(
    session: requests.Session, host: str, robots: RobotFileParser | None, *,
    timeout: float = 6,
) -> list[Evidence]:
    """Read at most one same-origin public sitemap; never recurse into sitemap indexes."""
    sitemap = f"https://{host}/sitemap.xml"
    if robots is not None and not robots.can_fetch("ace-hosts/0.1", sitemap):
        return []
    try:
        raw = _fetch(session, sitemap, timeout=timeout, attempts=1,
                     max_bytes=MAX_HTML_BYTES, content_type="xml")
        root = ET.fromstring(raw)
    except (OSError, ValueError, requests.RequestException, ET.ParseError):
        return []
    # A sitemap index points to other XML files; do not crawl them.
    if root.tag.rsplit("}", 1)[-1] != "urlset":
        return []
    matching_paths: set[str] = set()
    categories: set[str] = set()
    for elem in root.iter():
        if len(matching_paths) >= MAX_SITEMAP_URLS:
            break
        if elem.tag.rsplit("}", 1)[-1] != "loc" or not elem.text:
            continue
        try:
            url = urlsplit(elem.text.strip())
            if url.scheme != "https" or url.hostname != host or url.port not in (None, 443):
                continue
        except ValueError:
            continue
        thematic = site_categories(unquote(url.path))
        if not thematic:
            continue
        matching_paths.add(url.path)
        categories.update(thematic)
    if len(matching_paths) < 2:
        return []
    return [
        Evidence(host, None, category, (), "site-sitemap", None,
                 "web-lead", "sitemap-paths")
        for category in sorted(categories or {"manga"})
    ]


class PageRenderer(Protocol):
    def render(self, host: str, *, timeout: float = 6) -> str | None: ...


def scrape_site(
    session: requests.Session, host: str, joined: set[str], *,
    timeout: float = 6, browser: PageRenderer | None = None,
    scrape_sitemap: bool = True,
) -> list[Evidence]:
    """Inspect a catalog-nominated site; no bypass of robots or access controls.

    Scraping and sitemap URLs are review evidence only. They are never
    independent source votes and cannot enter a release without a merged PR.
    """
    if not safe_hostname(host) or not _public_dns_host(host):
        return []
    homepage = f"https://{host}/"
    robots: RobotFileParser | None = None
    try:
        robots_data = _fetch(session, f"https://{host}/robots.txt",
                             timeout=timeout, attempts=1, max_bytes=MAX_HTML_BYTES)
    except requests.HTTPError as error:
        if error.response is None or error.response.status_code != 404:
            raise
    else:
        robots = RobotFileParser()
        robots.parse(robots_data.decode("utf-8", errors="replace").splitlines())
        if not robots.can_fetch("ace-hosts/0.1", homepage):
            logger.info("robots.txt denies homepage scraping: %s", host)
            return []

    html = ""
    try:
        html = _fetch(session, homepage, timeout=timeout, attempts=1,
                      max_bytes=MAX_HTML_BYTES, content_type="text/html").decode(
                          "utf-8", errors="replace"
                      )
    except requests.HTTPError as error:
        if error.response is not None and error.response.status_code in (401, 403, 429):
            return []
        if browser is None:
            raise
    except (requests.RequestException, ValueError):
        if browser is None:
            raise

    page = SitePage()
    page.feed(html)
    categories = site_categories(page.topic_text)
    rendered_used = False
    if not categories and browser is not None:
        rendered = browser.render(host, timeout=timeout)
        if rendered:
            page = SitePage()
            page.feed(rendered)
            categories = site_categories(page.topic_text)
            rendered_used = bool(categories)
    results = [
        Evidence(host, page.title or None, category, _languages(page.language),
                 "site-homepage", None, "web-lead",
                 "html-rendered" if rendered_used else "html-title-description")
        for category in categories
    ]
    if scrape_sitemap:
        results.extend(fetch_sitemap_evidence(session, host, robots, timeout=timeout))
    if not results:
        return []
    for href in page.links:
        candidate = normalize_hostname(urljoin(homepage, href))
        if candidate and candidate != host and candidate in joined and safe_hostname(candidate):
            results.append(Evidence(candidate, None, "manga", (), "site-outbound",
                                    None, "web-lead", "href"))
    return results


def scrape_catalog_sites(session: requests.Session, records: list[Evidence], joined: set[str],
                         pro: set[str], already_approved: set[str], *,
                         max_sites: int, timeout: float = 6,
                         browser_fallback: bool = False, max_browser_sites: int = 15,
                         scrape_sitemaps: bool = True) -> list[Evidence]:
    """Select relevant catalog sites; reuse one optional, lazy Chromium process."""
    groups: dict[str, set[str]] = defaultdict(set)
    for record in records:
        if record.family not in {"derived", "web-lead"} and safe_hostname(record.hostname):
            groups[record.hostname].add(record.family)
    sites = [
        host for host, families in groups.items()
        if host not in pro and host not in already_approved
        and (host in joined or len(families) >= 2)
    ]
    sites.sort(key=lambda host: (-len(groups[host]), -int(host in joined), host))
    scraped: list[Evidence] = []
    browser = BrowserRenderer(max_sites=max_browser_sites) if browser_fallback else None
    try:
        for host in sites[:max_sites]:
            try:
                scraped.extend(scrape_site(
                    session, host, joined, timeout=timeout, browser=browser,
                    scrape_sitemap=scrape_sitemaps,
                ))
            except (OSError, ValueError, requests.RequestException) as error:
                logger.info("site %s skipped: %s", host, error)
    finally:
        if browser is not None:
            browser.close()
    logger.info("site scraping: %d homepages, %d browser fallbacks, %d evidence items",
                min(len(sites), max_sites), browser.attempts if browser else 0, len(scraped))
    return scraped


def classify(records: list[Evidence], joined: set[str], pro: set[str],
             allowed: set[str], excluded: set[str]) -> tuple[list[dict[str, object]], set[str]]:
    """Only cross-ecosystem corroborated or explicitly approved names qualify.

    Pro remains a manually curated category. Search/directory or derived
    catalogs *never* count towards independent corroboration.
    """
    group: dict[str, list[Evidence]] = defaultdict(list)
    for record in records:
        group[record.hostname].append(record)
    ledger: list[dict[str, object]] = []
    approved: set[str] = set()
    for host, evidence in sorted(group.items()):
        families = {record.family for record in evidence
                    if record.family not in {"derived", "web-lead"}}
        trusted = host in pro or host in allowed or (
            len(families) >= MIN_INDEPENDENT_FAMILIES
        )
        # The optional approval file allows explicitly reviewed hosts; never
        # allow it to override the global whitelist or shared-host safeguards.
        rejected = host in excluded or not safe_hostname(host)
        status = "excluded" if rejected else "pro" if host in pro else (
            "validated-extended" if trusted else "review"
        )
        if trusted and not rejected:
            approved.add(host)
        ledger.append({
            "hostname": host,
            "names": sorted({v.name for v in evidence if v.name}),
            "categories": sorted({v.category for v in evidence}),
            "languages": sorted({language for v in evidence for language in v.languages}),
            "repositories": sorted({v.repository for v in evidence}),
            "extension_ids": sorted({f"{v.repository}:{v.extension_id}" for v in evidence if v.extension_id}),
            "evidence": sorted({f"{v.repository}:{v.field}" for v in evidence}),
            "licenses": sorted({v.license for v in evidence if v.license is not None}),
            "independent_families": len(families),
            "joined": host in joined,
            "confidence": 0 if rejected else 100 if host in pro else
                          95 if host in allowed else
                          90 if len(families) >= 2 and host in joined else
                          80 if len(families) >= 2 else 30,
            "status": status,
        })
    for host in sorted(allowed - group.keys()):
        rejected = host in excluded or not safe_hostname(host)
        if not rejected:
            approved.add(host)
        ledger.append({
            "hostname": host, "names": [], "categories": [], "languages": [],
            "repositories": ["maintainer-reviewed"], "extension_ids": [],
            "evidence": ["seeds/anime-manga-extended.txt"], "licenses": [],
            "independent_families": 0, "joined": host in joined,
            "confidence": 0 if rejected else 95,
            "status": "excluded" if rejected else "validated-extended",
        })
    ledger.sort(key=lambda entry: str(entry["hostname"]))
    return ledger, approved


def _load_hostnames(path: Path | None) -> set[str]:
    return set(utils.iter_domains(path)) if path and path.is_file() else set()


def build_discovery(records: list[Evidence], *, joined_file: Path, pro_file: Path,
                    approved_file: Path, whitelist_file: Path, exceptions_file: Path,
                    output_dir: Path, checked_at: str) -> dict[str, int]:
    if not joined_file.is_file() or not pro_file.is_file():
        raise ValueError("current joined download and Pro list are required")
    joined = _load_hostnames(joined_file)
    pro = _load_hostnames(pro_file)
    if len(joined) < MIN_JOINED_DOMAINS or len(pro) < 100:
        raise ValueError("joined or Pro inputs unexpectedly small")
    allowed = _load_hostnames(approved_file)
    excluded = _load_hostnames(whitelist_file) | _load_hostnames(exceptions_file)
    ledger, eligible = classify(records, joined, pro, allowed, excluded)
    counts = {state: sum(entry["status"] == state for entry in ledger)
              for state in ("pro", "validated-extended", "review", "excluded")}
    # A broken or hostile upstream cannot silently trigger an enormous jump.
    additional = eligible - pro
    if len(additional) > max(3000, len(pro) // 2):
        raise ValueError("anomalous discovery growth: manual review required")
    output_dir.mkdir(parents=True, exist_ok=True)
    utils.atomic_write_lines(output_dir / "candidates.jsonl",
                             (json.dumps({"detected_at": checked_at, **row},
                                         ensure_ascii=False, sort_keys=True) for row in ledger))
    utils.atomic_write_lines(output_dir / "eligible-extended.txt", sorted(eligible))
    summary = {"catalog_candidates": len(ledger), "joined_input": len(joined),
               "joined_not_promoted": len(joined - pro - eligible),
               "pro_input": len(pro), "new_eligible": len(additional), **counts}
    utils.atomic_write_lines(output_dir / "summary.json",
                             [json.dumps(summary, sort_keys=True)])
    return summary


WEB_QUERIES: dict[str, str] = {
    "en": "manga scanlation webnovel reader",
    "it": "leggere manga anime fumetti online",
    "ja": "漫画 オンライン 読む",
    "ko": "웹툰 만화 보기",
    "zh": "漫画 在线 阅读",
    "id": "baca komik manga bahasa indonesia",
    "vi": "đọc truyện tranh manga",
    "th": "อ่านการ์ตูน มังงะ",
    "es": "leer manga webtoon en línea",
    "pt": "ler mangá webtoon online",
    "fr": "lire manga webtoon en ligne",
    "de": "manga online lesen",
    "ru": "читать мангу ранобэ онлайн",
    "tr": "manga oku webtoon",
    "ar": "قراءة مانجا روايات ويب",
}


def fetch_search_leads(session: requests.Session, queries: dict[str, str], *,
                       timeout: float) -> list[Evidence]:
    """Opt-in official Brave Search JSON API; results alone NEVER qualify.

    No SERP scraping, CAPTCHA workarounds or recursive URL traversal.
    Requires ACE_DISCOVERY_BRAVE_API_KEY set by the operator.
    """
    import os

    token = os.environ.get("ACE_DISCOVERY_BRAVE_API_KEY")
    if not token:
        raise ValueError("ACE_DISCOVERY_BRAVE_API_KEY is required")
    leads: list[Evidence] = []
    for lang, query in sorted(queries.items()):
        if lang not in SUPPORTED_LANGUAGES:
            raise ValueError("unsupported search language/query")
        if len(query) > 180:
            raise ValueError("query too long")
        url = ("https://api.search.brave.com/res/v1/web/search?q=" +
               quote(query, safe="") + "&count=20")
        request = requests.Request("GET", url, headers={"X-Subscription-Token": token})
        prepared = session.prepare_request(request)
        # _fetch is also used by catalog clients; a separate session carries
        # the documented search API header rather than injecting it globally.
        with requests.Session() as search_session:
            search_session.headers.update({
                "User-Agent": "ace-hosts/0.1", "X-Subscription-Token": token
            })
            payload = cast(object, json.loads(_fetch(search_session, prepared.url or url, timeout=timeout)))
        if not isinstance(payload, dict):
            raise ValueError("unexpected search API response")
        web = cast(dict[str, object], payload).get("web")
        results = cast(dict[str, object], web).get("results") if isinstance(web, dict) else None
        if not isinstance(results, list):
            raise ValueError("missing search results")
        for raw_result in cast(list[object], results[:20]):
            if not isinstance(raw_result, dict):
                continue
            result = cast(dict[str, object], raw_result)
            host = normalize_hostname(result.get("url"))
            if host and safe_hostname(host):
                name = result.get("title")
                leads.append(Evidence(
                    host, name if isinstance(name, str) else None,
                    "manga", (lang,), "brave-search", None, "web-lead", "url"
                ))
        time.sleep(1)
    return leads


class DiscoveryArgs(argparse.Namespace):
    output_dir: Path = utils.PROJECT_ROOT / "downloads" / "anime-discovery"
    downloads_dir: Path = utils.PROJECT_ROOT / "downloads"
    hosts_dir: Path = utils.PROJECT_ROOT / "hosts"
    cache_dir: Path = utils.PROJECT_ROOT / "downloads" / "anime-discovery-cache"
    refresh: bool = False
    require_core_catalogs: bool = False
    scrape_sites: bool = False
    max_scrape_sites: int = 60
    browser_fallback: bool = False
    max_browser_sites: int = 15
    scrape_sitemaps: bool = True
    directory_url: list[str] | None = None
    brave_search: bool = False
    queries_file: Path | None = None
    approved_file: Path = utils.PROJECT_ROOT / "seeds" / "anime-manga-extended.txt"
    whitelist: Path = utils.PROJECT_ROOT / "whitelist.txt"
    exceptions: Path = utils.PROJECT_ROOT / "exceptions" / "anime-manga.txt"
    timeout: float = 12


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DiscoveryArgs.output_dir)
    parser.add_argument("--downloads-dir", type=Path, default=DiscoveryArgs.downloads_dir)
    parser.add_argument("--hosts-dir", type=Path, default=DiscoveryArgs.hosts_dir)
    parser.add_argument("--cache-dir", type=Path, default=DiscoveryArgs.cache_dir)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--require-core-catalogs", action="store_true",
                        help="Fail if a core independent reader ecosystem is unavailable")
    parser.add_argument("--brave-search", action="store_true", help="Opt-in official JSON search API")
    parser.add_argument("--scrape-sites", action="store_true",
                        help="Scrape public HTML metadata of catalog-nominated anime sites")
    parser.add_argument("--max-scrape-sites", type=int, default=60,
                        help="Maximum HTTPS homepages to scrape (1..120, default 60)")
    parser.add_argument("--browser-fallback", action="store_true",
                        help="Use bounded headless Chromium if static HTML lacks thematic metadata")
    parser.add_argument("--max-browser-sites", type=int, default=15,
                        help="Limit dynamic browser rendering attempts (1..30, default 15)")
    parser.add_argument("--no-sitemaps", dest="scrape_sitemaps", action="store_false",
                        help="Do not inspect the same-origin /sitemap.xml")
    parser.add_argument("--queries-file", type=Path, help="JSON map of language to search query")
    parser.add_argument("--directory-url", action="append", default=[],
                        help="Opt-in public directory (one HTML page, robots respected)")
    parser.add_argument("--approved-file", type=Path, default=DiscoveryArgs.approved_file)
    parser.add_argument("--whitelist", type=Path, default=DiscoveryArgs.whitelist)
    parser.add_argument("--exceptions", type=Path, default=DiscoveryArgs.exceptions)
    parser.add_argument("--timeout", type=float, default=12)
    args = parser.parse_args(argv, namespace=DiscoveryArgs())
    if not 1 <= args.max_scrape_sites <= 120:
        parser.error("--max-scrape-sites must be between 1 and 120")
    if not 1 <= args.max_browser_sites <= 30:
        parser.error("--max-browser-sites must be between 1 and 30")
    utils.setup_logging()
    records: list[Evidence] = []
    succeeded: set[str] = set()
    with requests.Session() as session:
        session.headers.update({"User-Agent": "ace-hosts/0.1 (public catalog discovery)"})
        for catalog in CATALOGS:
            try:
                result = fetch_catalog(session, catalog, args.cache_dir,
                                       timeout=args.timeout, refresh=args.refresh)
                records.extend(result)
                succeeded.add(catalog.family)
                logger.info("%s: %d declared site URLs", catalog.name, len(result))
            except (OSError, ValueError, requests.RequestException, json.JSONDecodeError) as error:
                logger.warning("%s unavailable: %s", catalog.name, error)
        if args.brave_search:
            try:
                queries = WEB_QUERIES
                if args.queries_file is not None:
                    custom = cast(object, json.loads(args.queries_file.read_text(encoding="utf-8")))
                    if not isinstance(custom, dict):
                        raise ValueError("query file must be a language-to-query JSON object")
                    queries = cast(dict[str, str], custom)
                records.extend(fetch_search_leads(session, queries, timeout=args.timeout))
            except (OSError, ValueError, requests.RequestException) as error:
                logger.warning("web search disabled after error: %s", error)
        for url in args.directory_url or []:
            try:
                records.extend(fetch_directory(session, url, timeout=args.timeout))
            except (OSError, ValueError, requests.RequestException) as error:
                logger.warning("directory %s skipped: %s", url, error)
        if args.scrape_sites:
            joined = _load_hostnames(args.downloads_dir / "anime-manga-extended" / "newedgex-joined.txt")
            pro = _load_hostnames(args.hosts_dir / "anime-manga.txt")
            approved = _load_hostnames(args.approved_file)
            records.extend(scrape_catalog_sites(
                session, records, joined, pro, approved,
                max_sites=args.max_scrape_sites, timeout=min(args.timeout, 6),
                browser_fallback=args.browser_fallback,
                max_browser_sites=args.max_browser_sites,
                scrape_sitemaps=args.scrape_sitemaps,
            ))
    if args.require_core_catalogs and not {"mangayomi", "aidoku", "miru", "lnreader"} <= succeeded:
        logger.error("at least one core independent catalog is missing; refusing to publish")
        return 1
    if len(succeeded - {"derived"}) < MIN_INDEPENDENT_FAMILIES:
        logger.error("fewer than two independent catalogs succeeded; refusing to export")
        return 1
    try:
        summary = build_discovery(
            records, joined_file=args.downloads_dir / "anime-manga-extended" / "newedgex-joined.txt",
            pro_file=args.hosts_dir / "anime-manga.txt",
            approved_file=args.approved_file, whitelist_file=args.whitelist,
            exceptions_file=args.exceptions, output_dir=args.output_dir,
            checked_at=date.today().isoformat(),
        )
    except (ValueError, OSError) as error:
        logger.error("discovery failed: %s", error)
        return 1
    logger.info("discovery summary: %s", summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
