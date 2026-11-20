"""Web-original discovery does not depend on third-party blocklists."""
from __future__ import annotations

import json
from pathlib import Path

from ace_anime_discovery.anime_crawler import (
    CrawlConfig, PageResponse, WebCrawler, ranked_links,
)
from ace_anime_discovery.anime_discovery import Evidence, classify, content_page_path
from ace_anime_discovery.propose_anime_sites import propose_sites


def _review(tmp_path: Path, rows: list[dict[str, object]]) -> tuple[list[str], str]:
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    seed = tmp_path / "seeds.txt"
    seed.write_text("", encoding="utf-8")
    pro = tmp_path / "pro.txt"
    pro.write_text("", encoding="utf-8")
    body = tmp_path / "body.md"
    proposed = propose_sites(
        ledger, seed, pro, tmp_path / "whitelist",
        tmp_path / "exceptions", body,
    )
    assert sorted(seed.read_text(encoding="utf-8").splitlines()) == proposed
    return proposed, body.read_text(encoding="utf-8")


def test_crawler_discovers_unlisted_reader_and_verifies_actual_pages(
    tmp_path: Path,
) -> None:
    visited: list[str] = []
    host = "undiscovered-reader.example.org"

    def fetch(url: str, _budget: object, **_kwargs: object) -> PageResponse:
        visited.append(url)
        if url.endswith("/robots.txt"):
            return PageResponse(200, "text/plain", b"User-agent: *\nAllow: /\n")
        if url.endswith("/sitemap.xml"):
            return PageResponse(404, "text/plain", b"")
        if url == "https://directory.example.org/":
            return PageResponse(200, "text/html", (
                f'<title>Manga directory</title>'
                f'<a href="https://{host}/">New manga reader</a>'
            ).encode())
        if url == f"https://{host}/":
            return PageResponse(200, "text/html", (
                '<head><title>Independent Manga Reader</title>'
                '<meta name="description" content="Manga and webtoon series">'
                '</head><body>'
                '<a href="/series/one-piece">One Piece</a>'
                '<a href="/series/naruto">Naruto</a>'
                '<a href="https://1daydental.example.org/">Dental clinic</a>'
                '</body>'
            ).encode())
        if url == f"https://{host}/series/one-piece":
            return PageResponse(200, "text/html",
                                b"<title>One Piece - Chapter 100</title>"
                                + b'<div class="chapter-reader">'
                                + b'<img src="/pages/1.jpg"><img src="/pages/2.jpg">'
                                + b'</div>')
        if url == f"https://{host}/series/naruto":
            return PageResponse(200, "text/html",
                                b"<title>Naruto - Chapter 90</title>"
                                + b'<div class="chapter-reader">'
                                + b'<img src="/pages/a.jpg"><img src="/pages/b.jpg">'
                                + b'</div>')
        return PageResponse(404, "text/plain", b"")

    web = WebCrawler(
        CrawlConfig(tmp_path / "web.sqlite", max_requests=100,
                    max_depth=3, max_pages_domain=3, concurrency=2,
                    max_browser=0, delay=0),
        fetch=fetch,
    )
    try:
        web.seed([], directories=("https://directory.example.org/",),
                 with_public_indexes=False)
        web.run()
        records = web.frontier.records()
    finally:
        web.close()
    assert f"https://{host}/series/one-piece" in visited
    assert f"https://{host}/series/naruto" in visited
    assert not any("1daydental" in url for url in visited)
    assert all(
        record.family == "web-lead" for record in records if record.hostname == host
    )
    ledger, published = classify(records, set(), set(), set(), set())
    assert published == set()
    row = next(row for row in ledger if row["hostname"] == host)
    assert row["independent_families"] == 0
    assert row["joined"] is False
    assert row["onsite_content_pages"] == 2
    assert row["onsite_distinct_titles"] == 2
    assert row["verification_method"] == "multi-page-website"
    assert row["review_eligible"] is True
    assert row["status"] == "website-verified"
    proposed, body = _review(tmp_path, ledger)
    assert proposed == [host]
    assert "Web-original sites verified through multiple pages: **1**" in body
    assert "Catalog-supported sites: **0**" in body
    assert "| Domain |" not in body
    assert host not in body


def test_duplicate_query_parameters_do_not_simulate_two_content_pages(tmp_path: Path) -> None:
    host = "questionable.example.org"
    records = [
        Evidence(host, "Anime Reader", "anime", (), "site-homepage",
                 None, "web-lead", "html-title-description", origin=f"https://{host}/"),
        Evidence(host, None, "manga", (), "web-link",
                 None, "web-lead", "href", origin="https://directory.example.org/"),
        Evidence(host, "Story", "unknown", (), "site-internal-page",
                 None, "web-lead", "structured-content-page",
                 origin=f"https://{host}/series/one-piece?chapter=1"),
        Evidence(host, "Story", "unknown", (), "site-internal-page",
                 None, "web-lead", "structured-content-page",
                 origin=f"https://{host}/series/one-piece?chapter=2"),
    ]
    ledger, approved = classify(records, set(), set(), set(), set())
    assert approved == set()
    row = ledger[0]
    assert row["onsite_content_pages"] == 1
    assert row["review_eligible"] is False
    assert _review(tmp_path, ledger)[0] == []


def test_sitemap_hints_and_page_only_keyword_stuffing_are_not_enough(
    tmp_path: Path,
) -> None:
    host = "offtopic.example.org"
    records = [
        Evidence(host, "General web services", "unknown", (), "joined-candidate",
                 None, "web-lead", "domain"),
        Evidence(host, None, "manga", (), "site-sitemap",
                 None, "web-lead", "sitemap-paths"),
        Evidence(host, "Manga article", "manga", (), "site-internal-page",
                 None, "web-lead", "html-title-description",
                 origin=f"https://{host}/manga/article-one"),
        Evidence(host, "Anime article", "anime", (), "site-internal-page",
                 None, "web-lead", "html-title-description",
                 origin=f"https://{host}/anime/article-two"),
    ]
    ledger, _ = classify(records, set(), set(), set(), set())
    assert ledger[0]["onsite_content_pages"] == 2
    assert ledger[0]["review_eligible"] is False
    assert _review(tmp_path, ledger)[0] == []


def test_sitemap_paths_alone_are_not_content_pages() -> None:
    assert content_page_path("/manga/title-one")
    assert content_page_path("/series/one-piece")
    assert content_page_path("/en/manga/one-piece")
    assert content_page_path("/pt-BR/series/naruto")
    assert not content_page_path("/manga")
    assert not content_page_path("/manga-article")
    assert ranked_links(
        '<a href="/series/one-piece">One Piece</a>',
        "https://reader.example.org/",
    ) == ("https://reader.example.org/series/one-piece",)


def test_web_original_and_catalog_candidates_are_all_proposed(tmp_path: Path) -> None:
    def row(host: str, *, catalog: bool) -> dict[str, object]:
        return {
            "hostname": host,
            "status": "validated-candidate" if catalog else "website-verified",
            "independent_families": 2 if catalog else 0,
            "joined": False,
            "review_eligible": True,
            "verification_method": "independent-catalogs" if catalog else "multi-page-website",
            "evidence": (
                ["aidoku:baseURL"] if catalog else
                ["site-homepage:html-title-description", "site-internal-page:structured-content-page"]
            ),
            "onsite_content_pages": 0 if catalog else 2,
            "onsite_distinct_titles": 0 if catalog else 2,
            "onsite_content_urls": [] if catalog else [
                f"https://{host}/series/title-a", f"https://{host}/series/title-b"
            ],
        }
    candidates = [
        *[row(f"catalog{i}.example.org", catalog=True) for i in range(8)],
        *[row(f"web{i}.example.org", catalog=False) for i in range(8)],
    ]
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(
        "".join(json.dumps(item) + "\n" for item in candidates), encoding="utf-8"
    )
    seed = tmp_path / "approved.txt"
    seed.write_text("", encoding="utf-8")
    pro = tmp_path / "pro.txt"
    pro.write_text("", encoding="utf-8")
    proposed = propose_sites(
        ledger, seed, pro, tmp_path / "whitelist", tmp_path / "exceptions",
        tmp_path / "body.md",
    )
    assert len(proposed) == 16
    assert sum(host.startswith("web") for host in proposed) == 8
    assert sum(host.startswith("catalog") for host in proposed) == 8


def test_stale_cached_page_evidence_is_invalidated_on_policy_upgrade(
    tmp_path: Path,
) -> None:
    from ace_anime_discovery.anime_crawler import Frontier

    db = tmp_path / "legacy.sqlite"
    old = Frontier(db)
    try:
        old.add("https://stale.example.org/", depth=0, source="brave-search:en")
        for url in ("https://stale.example.org/series/first",
                    "https://stale.example.org/series/second"):
            old.add(url, depth=1, source="internal:stale.example.org")
            old.evidence(Evidence(
                "stale.example.org", "Manga chapter", "manga", (),
                "site-internal-page", None, "web-lead",
                "html-title-description", origin=url,
            ))
        old.evidence(Evidence(
            "stale.example.org", "Manga homepage", "manga", (),
            "site-homepage", None, "web-lead",
            "html-title-description", origin="https://stale.example.org/",
        ))
        old.evidence(Evidence(
            "stale.example.org", "Manga search result", "manga", (),
            "brave-search", None, "web-lead", "url",
        ))
        # Their previous monthly recheck is far in the future.
        old.conn.execute(
            "UPDATE frontier SET status='done',next_check=9999999999"
        )
        old.conn.execute(
            "DELETE FROM run_state WHERE key='site_evidence_policy_version'"
        )
    finally:
        old.close()

    reopened = Frontier(db)
    try:
        assert {record.repository for record in reopened.records()} == {"brave-search"}
        assert "stale.example.org" in reopened.seen_hosts()
        due = [reopened.claim(1, 3)[0].url for _ in range(3)]
        assert set(due) == {
            "https://stale.example.org/",
            "https://stale.example.org/series/first",
            "https://stale.example.org/series/second",
        }
    finally:
        reopened.close()


def test_two_distinct_urls_with_identical_parking_titles_are_not_accepted(
    tmp_path: Path,
) -> None:
    host = "reused-template.example.org"
    records = [
        Evidence(host, "Watch anime", "anime", (), "site-homepage",
                 None, "web-lead", "html-title-description", origin=f"https://{host}/"),
        Evidence(host, None, "manga", (), "web-link",
                 None, "web-lead", "href", origin="https://directory.example.org/"),
        Evidence(host, "Generic Manga Landing Page", "unknown", (),
                 "site-internal-page", None, "web-lead",
                 "structured-content-page", origin=f"https://{host}/series/first"),
        Evidence(host, "Generic Manga Landing Page", "unknown", (),
                 "site-internal-page", None, "web-lead",
                 "structured-content-page", origin=f"https://{host}/series/second"),
    ]
    ledger, _ = classify(records, set(), set(), set(), set())
    assert ledger[0]["onsite_content_pages"] == 2
    assert ledger[0]["onsite_distinct_titles"] == 1
    assert ledger[0]["review_eligible"] is False
    assert _review(tmp_path, ledger)[0] == []


def test_failed_internal_recheck_revokes_multi_page_qualification(
    tmp_path: Path,
) -> None:
    from ace_anime_discovery.anime_crawler import CrawlPage, InspectedPage

    host = "reader.example.org"
    url = f"https://{host}/series/one-piece"
    web = WebCrawler(
        CrawlConfig(tmp_path / "revoked.sqlite", max_browser=0, delay=0),
    )
    try:
        web.frontier.add(url, depth=1, source=f"internal:{host}")
        web.frontier.evidence(Evidence(
            host, "One Piece - Chapter 40", "unknown", (),
            "site-internal-page", None, "web-lead",
            "structured-content-page", origin=url,
        ))
        assert any(e.repository == "site-internal-page" for e in web.frontier.records())
        web._record(  # pyright: ignore[reportPrivateUsage]
            InspectedPage(CrawlPage(url, 1, f"internal:{host}", "lead"),
                          "", (), (), (), (), "access denied"),
            None,
        )
        assert not any(e.repository == "site-internal-page" for e in web.frontier.records())
    finally:
        web.close()
