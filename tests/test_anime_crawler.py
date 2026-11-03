"""Offline tests for cross-catalog discovery, safety, persistence and PR gating."""
from __future__ import annotations

import json
import socket
from pathlib import Path
import pytest

from scripts import anime_discovery as discovery
from scripts import anime_crawler as crawler
from scripts.anime_search import parse_brave_results, search_queries
from scripts.propose_anime_sites import propose_sites


@pytest.mark.parametrize(("url", "expected"), [
    ("https://bücher.example.org/a#fragment", "https://xn--bcher-kva.example.org/a"),
    ("https://reader.example.org/%EC%9B%B9%ED%88%B0/", "https://reader.example.org/%EC%9B%B9%ED%88%B0/"),
    ("https://reader.example.org/?utm_source=spam&chapter=7", "https://reader.example.org/?chapter=7"),
    ("http://reader.example.org/", None),
    ("https://user:pass@reader.example.org/", None),
    ("https://reader.example.org:444/", None),
    ("https://127.0.0.1/", None),
    ("https://localhost/", None),
    ("https://reader.example.org/\nX-Header: injected", None),
])
def test_url_normalization(url: str, expected: str | None) -> None:
    assert crawler.normalize_url(url) == expected


def test_search_expands_categories_and_multilingual_writing() -> None:
    values = list(search_queries(languages=("ja", "ko", "zh-hans", "zh-hant", "ar"),
                                 categories=("manga", "webtoon"), max_queries=10))
    assert len(values) == 10
    assert any("漫画" in query for lang, query in values if lang == "ja")
    assert any("웹툰" in query for lang, query in values if lang == "ko")
    assert any("條漫" in query for lang, query in values if lang == "zh-hant")
    assert len(list(search_queries(max_queries=3))) == 3
    with pytest.raises(ValueError):
        list(search_queries(languages=("xx",)))


def test_official_search_json_parser() -> None:
    payload: object = {"web": {"results": [
        {"url": "https://unseen.example.org/", "title": "Manga collection"},
        {"url": "http://unsafe.example.org/", "title": "Wrong transport"},
        {"url": "https://second.example.org/", "title": "Webtoon"},
    ]}}
    assert parse_brave_results(payload) == [
        ("https://unseen.example.org/", "Manga collection"),
        ("https://second.example.org/", "Webtoon"),
    ]
    with pytest.raises(ValueError):
        parse_brave_results({"web": {}})


def test_dns_rebinding_guard_refuses_mixed_public_private(monkeypatch: pytest.MonkeyPatch) -> None:
    def mixed_dns(_host: str, _port: int, *, type: int) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        return [(socket.AF_INET, type, 6, "", ("8.8.8.8", 443)),
                (socket.AF_INET, type, 6, "", ("127.0.0.1", 443))]
    monkeypatch.setattr(socket, "getaddrinfo", mixed_dns)
    with pytest.raises(ValueError, match="non-public"):
        crawler.resolved_public_addresses("safe.example.org")
    budget = crawler.RequestBudget(1, 5)
    with pytest.raises(ValueError):
        crawler.safe_get("https://safe.example.org/", budget)
    assert budget.requests == 0


def test_persistent_priority_frontier_resume(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite"
    store = crawler.Frontier(db)
    assert store.add("https://first.example.org/", depth=2, source="link", priority=10)
    assert not store.add("https://first.example.org/#again", depth=2, source="link")
    assert store.add("https://second.example.org/", depth=0, source="search", priority=90)
    assert not store.add("https://127.0.0.1/", depth=0, source="invalid")
    batch = store.claim(1, 2)
    assert batch[0].url == "https://second.example.org/"
    store.close()
    reopened = crawler.Frontier(db)
    assert reopened.claim(2, 2)[0].url == "https://second.example.org/"
    reopened.close()


def test_recursive_directory_finds_unknown_site_and_requires_human_review(tmp_path: Path) -> None:
    visited: list[str] = []

    def fake_fetch(url: str, _budget: crawler.RequestBudget, **_kwargs: object) -> crawler.PageResponse:
        visited.append(url)
        if url.endswith("/robots.txt"):
            return crawler.PageResponse(200, "text/plain", b"User-agent: *\nAllow: /\n")
        if url == "https://directory.example.org/":
            return crawler.PageResponse(200, "text/html", (
                b'<html><title>Manga webtoon directory</title><body>'
                b'<a href="https://unknown-reader.example.org/">New source</a>'
                b'<a href="https://github.com/">Infrastructure</a></body></html>'
            ))
        if url == "https://unknown-reader.example.org/":
            return crawler.PageResponse(200, "text/html", (
                '<html lang="ko"><head><title>만화 웹툰 웹소설 reader</title>'
                '<meta property="og:description" content="웹툰 만화">'
                '</head></html>'
            ).encode())
        return crawler.PageResponse(404, "text/plain", b"")

    config = crawler.CrawlConfig(tmp_path / "frontier.sqlite", max_requests=20,
                                 max_depth=2, concurrency=2, max_browser=0, delay=0)
    web = crawler.WebCrawler(config, fetch=fake_fetch)
    try:
        web.seed([], directories=("https://directory.example.org/",),
                 with_public_indexes=False)
        stats = web.run()
        evidence = web.frontier.records()
    finally:
        web.close()
    assert stats["websites_verified"] == 1
    assert "https://unknown-reader.example.org/" in visited
    assert not any(e.hostname == "github.com" for e in evidence)
    assert {e.repository for e in evidence if e.hostname == "unknown-reader.example.org"} == {
        "web-link", "site-homepage",
    }
    ledger, approved = discovery.classify(evidence, set(), set(), set(), set())
    row = next(x for x in ledger if x["hostname"] == "unknown-reader.example.org")
    assert row["status"] == "website-verified"
    assert row["verification_method"] == "website-metadata"
    assert row["confidence"] == 72
    assert approved == set()  # No publication authority.

    # The PR generator may propose this domain without a single catalog vote.
    report = tmp_path / "candidates.jsonl"
    report.write_text("\n".join(json.dumps(r) for r in ledger), encoding="utf-8")
    approved_file = tmp_path / "review-seeds.txt"
    pro_file = tmp_path / "pro.txt"
    approved_file.write_text("", encoding="utf-8")
    pro_file.write_text("existing-pro.example.org\n", encoding="utf-8")
    body = tmp_path / "body.md"
    proposals = propose_sites(report, approved_file, pro_file, tmp_path / "whitelist",
                              tmp_path / "exceptions", body)
    assert proposals == ["unknown-reader.example.org"]
    assert "Website-verified" in body.read_text(encoding="utf-8")
    assert "website-metadata" in body.read_text(encoding="utf-8")


def test_request_and_depth_budget(tmp_path: Path) -> None:
    budget = crawler.RequestBudget(2, 5)
    assert budget.consume() and budget.consume() and not budget.consume()
    conf = crawler.CrawlConfig(tmp_path / "limits.sqlite", max_depth=0,
                               max_requests=3, concurrency=1, delay=0,
                               max_browser=0)
    seen: list[str] = []

    def fetch(url: str, _budget: crawler.RequestBudget, **_kw: object) -> crawler.PageResponse:
        seen.append(url)
        if url.endswith("/robots.txt"):
            return crawler.PageResponse(200, "text/plain", b"User-agent: *\nAllow: /\n")
        return crawler.PageResponse(200, "text/html", (
            b'<title>Manga site</title><a href="https://nextsite.example.org/">Next</a>'
        ))
    web = crawler.WebCrawler(conf, fetch=fetch)
    try:
        web.seed([], directories=("https://top.example.org/",),
                 with_public_indexes=False)
        web.run()
        assert all("nextsite.example.org" not in url for url in seen)
        assert web.frontier.statistics()["urls_discovered"] == 1
    finally:
        web.close()
