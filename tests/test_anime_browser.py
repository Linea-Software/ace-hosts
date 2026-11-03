"""Offline security and dynamic-site discovery tests."""
from __future__ import annotations

import socket

import pytest
import requests

from scripts import anime_discovery as discovery
from scripts import anime_browser
from scripts.anime_browser import BrowserRenderer


@pytest.mark.parametrize(("url", "allowed"), [
    ("https://reader.example.org/", True),
    ("https://reader.example.org/manga/page", True),
    ("https://reader.example.org:443/assets/app.js", True),
    ("http://reader.example.org/", False),
    ("https://reader.example.org:8443/", False),
    ("https://reader.example.org.evil.test/", False),
    ("https://evil.example.org/", False),
    ("https://user:pass@reader.example.org/", False),
    ("file:///etc/passwd", False),
])
def test_browser_same_origin_guard(url: str, allowed: bool):
    assert anime_browser.same_origin_https(url, "reader.example.org") is allowed


def test_browser_refuses_private_resolutions(monkeypatch: pytest.MonkeyPatch):
    def private_dns(_host: str, _port: int, *, type: int):
        return [(2, type, 6, "", ("127.0.0.1", 443))]
    monkeypatch.setattr(socket, "getaddrinfo", private_dns)
    assert not anime_browser.is_public_dns("site.example.org")
    with BrowserRenderer() as renderer:
        assert renderer.render("site.example.org") is None
        assert renderer.attempts == 0


def test_playwright_fallback_renders_js_metadata(monkeypatch: pytest.MonkeyPatch):
    urls: list[str] = []
    def fake_fetch(_session: requests.Session, url: str, **_kwargs: object) -> bytes:
        urls.append(url)
        if url.endswith("robots.txt"):
            return b"User-agent: *\nAllow: /\n"
        return b"<html><title>Loading application</title><main id='app'></main></html>"

    class Renderer:
        def __init__(self) -> None:
            self.called: int = 0

        def render(self, host: str, *, timeout: float = 6) -> str:
            assert host == "reader.example.org"
            assert timeout > 0
            self.called += 1
            return (
                '<html lang="ko"><head><meta property="og:title" '
                'content="만화 웹툰 Reader"></head><body><h1>Webtoon</h1></body></html>'
            )

    renderer = Renderer()
    def public(_host: str) -> bool:
        return True

    monkeypatch.setattr(discovery, "_public_dns_host", public)
    monkeypatch.setattr(discovery, "_fetch", fake_fetch)
    with requests.Session() as session:
        rows = discovery.scrape_site(session, "reader.example.org", set(),
                                     browser=renderer)
    assert renderer.called == 1
    assert {row.category for row in rows} == {"manhwa", "webtoon"}
    assert all(row.languages == ("ko",) for row in rows)
    assert all(row.field == "html-rendered" for row in rows)
    assert "https://reader.example.org/" in urls


def test_sitemap_confirms_thematic_paths_without_crawling(monkeypatch: pytest.MonkeyPatch):
    urls: list[str] = []
    def fake_fetch(_session: requests.Session, url: str, **_kwargs: object) -> bytes:
        urls.append(url)
        if url.endswith("/robots.txt"):
            return b"User-agent: *\nAllow: /\n"
        if url.endswith("/sitemap.xml"):
            return (
                b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                b'<url><loc>https://reader.example.org/manga/a</loc></url>'
                b'<url><loc>https://reader.example.org/manga/b</loc></url>'
                b'<url><loc>https://evil.example.org/manga/third</loc></url>'
                b'<url><loc>http://reader.example.org/manga/fourth</loc></url>'
                b'</urlset>'
            )
        return b"<html><title>Loading application</title></html>"

    def public(_host: str) -> bool:
        return True

    monkeypatch.setattr(discovery, "_public_dns_host", public)
    monkeypatch.setattr(discovery, "_fetch", fake_fetch)
    with requests.Session() as session:
        rows = discovery.scrape_site(session, "reader.example.org", set())
    assert len(rows) == 1
    assert rows[0].field == "sitemap-paths"
    assert rows[0].category == "manga"
    assert urls == [
        "https://reader.example.org/robots.txt",
        "https://reader.example.org/",
        "https://reader.example.org/sitemap.xml",
    ]


def test_robots_denial_blocks_sitemap_and_browser(monkeypatch: pytest.MonkeyPatch):
    urls: list[str] = []
    def fake_fetch(_session: requests.Session, url: str, **_kwargs: object) -> bytes:
        urls.append(url)
        return b"User-agent: *\nDisallow: /\n"

    class Renderer:
        def render(self, host: str, *, timeout: float = 6) -> None:
            assert timeout > 0 and host == "reader.example.org"
            pytest.fail("browser must not run when robots denies access")

    def public(_host: str) -> bool:
        return True

    monkeypatch.setattr(discovery, "_public_dns_host", public)
    monkeypatch.setattr(discovery, "_fetch", fake_fetch)
    with requests.Session() as session:
        assert discovery.scrape_site(
            session, "reader.example.org", set(), browser=Renderer()
        ) == []
    assert urls == ["https://reader.example.org/robots.txt"]


def test_access_denied_never_uses_browser(monkeypatch: pytest.MonkeyPatch):
    def fake_fetch(_session: requests.Session, url: str, **_kwargs: object) -> bytes:
        if url.endswith("/robots.txt"):
            return b"User-agent: *\nAllow: /\n"
        response = requests.Response()
        response.status_code = 403
        raise requests.HTTPError("Forbidden", response=response)

    class Renderer:
        def render(self, host: str, *, timeout: float = 6) -> None:
            assert timeout > 0 and host == "reader.example.org"
            pytest.fail("browser must not bypass access restrictions")

    def public(_host: str) -> bool:
        return True

    monkeypatch.setattr(discovery, "_public_dns_host", public)
    monkeypatch.setattr(discovery, "_fetch", fake_fetch)
    with requests.Session() as session:
        assert discovery.scrape_site(
            session, "reader.example.org", set(), browser=Renderer()
        ) == []


def test_sitemap_percent_encoded_multilingual_paths(monkeypatch: pytest.MonkeyPatch):
    def fake_fetch(_session: requests.Session, url: str, **_kwargs: object) -> bytes:
        if url.endswith("/robots.txt"):
            return b"User-agent: *\nAllow: /\n"
        if url.endswith("/sitemap.xml"):
            return (
                b"<urlset><url><loc>https://reader.example.org/%EC%9B%B9%ED%88%B0/one</loc></url>"
                b"<url><loc>https://reader.example.org/%EC%9B%B9%ED%88%B0/two</loc></url></urlset>"
            )
        return b"<html><title>Reader</title></html>"

    def public(_host: str) -> bool:
        return True

    monkeypatch.setattr(discovery, "_fetch", fake_fetch)
    monkeypatch.setattr(discovery, "_public_dns_host", public)
    with requests.Session() as session:
        evidence = discovery.scrape_site(session, "reader.example.org", set())
    assert [(item.repository, item.category) for item in evidence] == [
        ("site-sitemap", "webtoon")
    ]
