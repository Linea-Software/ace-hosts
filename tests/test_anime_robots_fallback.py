"""Robots fallback and sensitive-endpoint protections, without network."""
from __future__ import annotations

import ssl
from pathlib import Path

import pytest

from ace_anime_discovery.anime_crawl_policy import public_crawl_url
from ace_anime_discovery.anime_crawler import (
    CrawlConfig, CrawlPage, PageResponse, WebCrawler, ranked_links,
)


@pytest.mark.parametrize("url", [
    "https://reader.example.org/",
    "https://www.reader.example.org/manga/naruto",
    "https://reader.example.org/series/accounting",
    "https://reader.example.org/manga/authors",
    "https://reader.example.org/pt-BR/series/manga-title",
    "https://reader.example.org/sitemap.xml",
    "https://reader.example.org/_next/static/chunks/app.js",
    "https://reader.example.org/_nuxt/entry.js",
    "https://reader.example.org/series/naruto?chapter=5",
])
def test_public_pages_are_allowed(url: str) -> None:
    assert public_crawl_url(url)


@pytest.mark.parametrize("url", [
    "http://reader.example.org/",
    "https://api.reader.example.org/",
    "https://auth.reader.example.org/",
    "https://reader.example.org/login",
    "https://reader.example.org/signup?from=manga",
    "https://reader.example.org/manga/admin/users",
    "https://reader.example.org/Api/V1/manga",
    "https://reader.example.org/manga/graphql",
    "https://reader.example.org/manga/swagger",
    "https://reader.example.org/oauth2/callback?code=abc",
    "https://reader.example.org/%2561pi/v2",
    "https://reader.example.org/manga%2F..%2Fapi%2Fusers",
    "https://reader.example.org/manga%5clogin",
    "https://reader.example.org/manga/novel?token=secret",
    "https://reader.example.org/series/novel?access%5ftoken=secret",
    "https://reader.example.org/series/novel?auth=required",
    "https://reader.example.org/series/novel?sessionid=abc",
    "https://reader.example.org/series/novel?email=test%40example.org",
    "https://reader.example.org/admin.php",
    "https://reader.example.org/.git/config",
    "https://reader.example.org/settings/account",
    "https://reader.example.org/manga/backup.sql",
    "https://reader.example.org/novel/reader.json",
    "https://reader.example.org/_next/data/123/novel.json",
])
def test_sensitive_pages_are_rejected(url: str) -> None:
    assert not public_crawl_url(url), url


def test_link_frontier_rejects_sensitive_urls(tmp_path: Path) -> None:
    html = (
        '<a href="/manga/naruto">Manga Naruto</a>'
        '<a href="/api/manga">Manga API</a>'
        '<a href="/login?next=/manga">Manga login</a>'
        '<a href="/manga/naruto?token=secret">Manga token</a>'
        '<a href="https://api.elsewhere.example.org/manga">Manga API</a>'
    )
    links = ranked_links(html, "https://reader.example.org/")
    assert links == ("https://reader.example.org/manga/naruto",)
    web = WebCrawler(CrawlConfig(tmp_path / "state.sqlite", max_browser=0, delay=0))
    try:
        assert web.frontier.add(
            "https://reader.example.org/manga/naruto", depth=1, source="internal"
        )
        assert not web.frontier.add(
            "https://reader.example.org/api/manga", depth=1, source="internal"
        )
        assert not web.frontier.add(
            "https://reader.example.org/manga/naruto?token=secret",
            depth=1, source="internal",
        )
        assert web.frontier.seen_hosts() == {"reader.example.org"}
        # Reject legacy queue entries before any robots or page request.
        assert not web._allowed(  # pyright: ignore[reportPrivateUsage]
            "https://reader.example.org/oauth/callback"
        )
    finally:
        web.close()


@pytest.mark.parametrize(("status", "content_type", "body"), [
    (404, "text/plain", b"Not Found"),
    (410, "text/plain", b"Gone"),
    (200, "text/html", b"<html><head><title>404 Not Found</title></head></html>"),
    (200, "text/plain", b"<html><title>File not found</title></html>"),
])
def test_missing_robots_allows_only_safe_public_pages(
    tmp_path: Path, status: int, content_type: str, body: bytes,
) -> None:
    urls: list[str] = []

    def fetch(url: str, _budget: object, **_kwargs: object) -> PageResponse:
        urls.append(url)
        return PageResponse(status, content_type, body)

    web = WebCrawler(
        CrawlConfig(tmp_path / f"missing-{status}-{len(body)}.sqlite",
                    max_browser=0, delay=0),
        fetch=fetch,
    )
    try:
        assert web._allowed("https://reader.example.org/")  # pyright: ignore[reportPrivateUsage]
        assert web._allowed(  # pyright: ignore[reportPrivateUsage]
            "https://reader.example.org/manga/naruto"
        )
        assert not web._allowed(  # pyright: ignore[reportPrivateUsage]
            "https://reader.example.org/api/v1/manga"
        )
        assert not web._allowed(  # pyright: ignore[reportPrivateUsage]
            "https://reader.example.org/manga/naruto?token=secret"
        )
        assert urls == ["https://reader.example.org/robots.txt"]
        assert web._robots_missing == {"reader.example.org"}  # pyright: ignore[reportPrivateUsage]
    finally:
        web.close()


@pytest.mark.parametrize(("status", "content_type", "body", "reason"), [
    (301, "text/plain", b"", "robots-http-301"),
    (302, "text/plain", b"", "robots-http-302"),
    (401, "text/plain", b"", "robots-access-denied"),
    (403, "text/plain", b"", "robots-access-denied"),
    (429, "text/plain", b"", "robots-rate-limited"),
    (503, "text/plain", b"", "robots-temporary-error"),
    (200, "text/html", b"<title>Sign in to continue</title>", "robots-invalid-content"),
])
def test_robots_not_found_is_not_confused_with_access_denial(
    tmp_path: Path, status: int, content_type: str, body: bytes, reason: str,
) -> None:
    seen: list[str] = []

    def fetch(url: str, _budget: object, **_kwargs: object) -> PageResponse:
        seen.append(url)
        return PageResponse(status, content_type, body)

    web = WebCrawler(
        CrawlConfig(tmp_path / f"blocked-{status}.sqlite", max_browser=0, delay=0),
        fetch=fetch,
    )
    try:
        assert not web._allowed("https://reader.example.org/")  # pyright: ignore[reportPrivateUsage]
        assert not web._allowed(  # pyright: ignore[reportPrivateUsage]
            "https://reader.example.org/manga/title"
        )
        assert all(url == "https://reader.example.org/robots.txt" for url in seen)
        assert web._robots_failures["reader.example.org"] == reason  # pyright: ignore[reportPrivateUsage]
    finally:
        web.close()


def test_robots_disallow_is_never_overridden(tmp_path: Path) -> None:
    def fetch(url: str, _budget: object, **_kwargs: object) -> PageResponse:
        assert url.endswith("/robots.txt")
        return PageResponse(
            200, "text/plain",
            b"User-agent: *\nDisallow: /manga/private\nAllow: /\n",
        )

    web = WebCrawler(
        CrawlConfig(tmp_path / "disallowed.sqlite", max_browser=0, delay=0),
        fetch=fetch,
    )
    try:
        assert web._allowed(  # pyright: ignore[reportPrivateUsage]
            "https://reader.example.org/manga/public"
        )
        assert not web._allowed(  # pyright: ignore[reportPrivateUsage]
            "https://reader.example.org/manga/private"
        )
        assert not web._allowed(  # pyright: ignore[reportPrivateUsage]
            "https://reader.example.org/api/v1"
        )
    finally:
        web.close()


def test_soft_404_detection_never_overrides_robots_directives(tmp_path: Path) -> None:
    def fetch(url: str, _budget: object, **_kwargs: object) -> PageResponse:
        assert url.endswith("/robots.txt")
        return PageResponse(
            200, "text/plain",
            b"User-agent: *\nDisallow: /\n<title>404 Not Found</title>\n",
        )

    web = WebCrawler(
        CrawlConfig(tmp_path / "robots-with-title.sqlite", max_browser=0, delay=0),
        fetch=fetch,
    )
    try:
        assert not web._allowed("https://reader.example.org/")  # pyright: ignore[reportPrivateUsage]
        assert not web._robots_missing  # pyright: ignore[reportPrivateUsage]
    finally:
        web.close()


def test_soft_404_login_challenge_is_denied(tmp_path: Path) -> None:
    def fetch(url: str, _budget: object, **_kwargs: object) -> PageResponse:
        assert url.endswith("/robots.txt")
        return PageResponse(
            200, "text/html",
            b"<html><title>Sign in to continue</title></html>",
        )

    web = WebCrawler(
        CrawlConfig(tmp_path / "login-challenge.sqlite", max_browser=0, delay=0),
        fetch=fetch,
    )
    try:
        assert not web._allowed("https://reader.example.org/")  # pyright: ignore[reportPrivateUsage]
        assert web._robots_failures["reader.example.org"] == "robots-invalid-content"  # pyright: ignore[reportPrivateUsage]
    finally:
        web.close()


def test_tls_failure_does_not_trigger_missing_robots_fallback(tmp_path: Path) -> None:
    urls: list[str] = []

    def fetch(url: str, _budget: object, **_kwargs: object) -> PageResponse:
        urls.append(url)
        raise ssl.SSLCertVerificationError("unable to get local issuer certificate")

    web = WebCrawler(
        CrawlConfig(tmp_path / "tls.sqlite", max_browser=0, delay=0),
        fetch=fetch,
    )
    try:
        assert not web._allowed("https://reader.example.org/")  # pyright: ignore[reportPrivateUsage]
        assert web._robots_failures["reader.example.org"] == "robots-tls-error"  # pyright: ignore[reportPrivateUsage]
        assert urls == ["https://reader.example.org/robots.txt"]
    finally:
        web.close()


def test_end_to_end_crawls_home_and_content_without_robots(
    tmp_path: Path,
) -> None:
    host = "unlisted-reader.example.org"
    fetched: list[str] = []

    def fetch(url: str, _budget: object, **_kwargs: object) -> PageResponse:
        fetched.append(url)
        if url.endswith("/robots.txt"):
            return PageResponse(410, "text/plain", b"Gone")
        if url.endswith("/sitemap.xml"):
            return PageResponse(404, "text/plain", b"Not found")
        if url == f"https://{host}/":
            return PageResponse(
                200, "text/html",
                b'<title>Manga reader</title>'
                + b'<a href="/series/naruto">Manga Naruto</a>'
                + b'<a href="/series/one-piece">Manga One Piece</a>'
                + b'<a href="/api/manga">Manga API</a>'
                + b'<a href="/login">Manga login</a>',
            )
        if url == f"https://{host}/series/naruto":
            return PageResponse(200, "text/html", b"<title>Naruto Chapter 1</title>")
        if url == f"https://{host}/series/one-piece":
            return PageResponse(200, "text/html", b"<title>One Piece Chapter 1</title>")
        raise AssertionError(f"unexpected unsafe request: {url}")

    web = WebCrawler(
        CrawlConfig(
            tmp_path / "endtoend.sqlite", max_requests=50, max_depth=2,
            max_pages_domain=3, concurrency=2, max_browser=0, delay=0,
        ), fetch=fetch,
    )
    try:
        # Search-discovered websites receive no independent catalog vote.
        assert web.frontier.add(
            f"https://{host}/", depth=0, source="brave-search:en", priority=85
        )
        result = web.run()
        urls = web.frontier.seen_hosts()
    finally:
        web.close()
    assert f"https://{host}/series/naruto" in fetched
    assert f"https://{host}/series/one-piece" in fetched
    assert not any("/api/" in url or "/login" in url for url in fetched)
    assert result["robots_missing_hosts"] == 1
    assert urls == {host}


def test_sensitive_legacy_frontier_url_is_not_requested(tmp_path: Path) -> None:
    fetched: list[str] = []

    def fetch(url: str, _budget: object, **_kwargs: object) -> PageResponse:
        fetched.append(url)
        raise AssertionError("unsafe URL must not be requested")

    web = WebCrawler(CrawlConfig(tmp_path / "legacy.sqlite", max_browser=0, delay=0),
                     fetch=fetch)
    try:
        # Simulate URL inserted before the safe-path policy was deployed.
        web.frontier.conn.execute(
            "INSERT INTO frontier(url,host,depth,priority,source,kind)"
            + " VALUES(?,?,?,?,?,?)",
            ("https://reader.example.org/api/v1", "reader.example.org",
             1, 50, "internal:reader.example.org", "lead"),
        )
        outcome = web._inspect(  # pyright: ignore[reportPrivateUsage]
            CrawlPage("https://reader.example.org/api/v1", 1, "internal", "lead")
        )
        assert outcome.error == "sensitive-url"
        assert fetched == []
    finally:
        web.close()
