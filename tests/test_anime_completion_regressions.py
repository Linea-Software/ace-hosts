"""Offline regressions for evidence completion, safe redirects and review gating."""
from __future__ import annotations

from pathlib import Path

from ace_anime_discovery.anime_crawler import (
    CrawlConfig, Frontier, PageResponse, WebCrawler,
)
from ace_anime_discovery.anime_diagnostics import candidate_decision
from ace_anime_discovery.anime_discovery import Evidence, classify


def _content(host: str, series: int) -> Evidence:
    return Evidence(
        host, f"Distinct Manga Series {series}", "manga", (),
        "site-internal-page", None, "web-lead", "verified-content-page",
        origin=f"https://{host}/series/{series}",
    )


def test_catalog_and_two_verified_pages_need_only_human_review() -> None:
    host = "catalog-reader.example.org"
    evidence = [
        Evidence(host, "Manga Reader", "manga", (), "aidoku",
                 None, "aidoku", "baseURL"),
        _content(host, 1), _content(host, 2),
    ]
    rows, approved = classify(evidence, set(), set(), set(), set())
    assert approved == set()
    row = rows[0]
    assert row["status"] == "review"
    assert row["review_eligible"] is True
    assert row["release_approved"] is False
    assert row["eligibility_path"] == "catalog-verified-pages"


def test_homepage_completion_can_exceed_internal_page_quota(tmp_path: Path) -> None:
    frontier = Frontier(tmp_path / "state.sqlite")
    host = "reader.example.org"
    try:
        for idx in range(3):
            assert frontier.add(
                f"https://{host}/manga/{idx}", depth=1,
                source=f"internal:{host}", priority=90,
            )
        for _ in range(3):
            page = frontier.claim(1, max_pages_domain=3)
            assert len(page) == 1
            frontier.complete(page[0])
        frontier.evidence(_content(host, 1))
        frontier.evidence(_content(host, 2))
        assert frontier.prioritize_promising_homepages() == 1
        page = frontier.claim(1, max_pages_domain=3)
        assert len(page) == 1
        assert page[0].url == f"https://{host}/"
        assert page[0].source == "verification-homepage"
        frontier.complete(page[0])
        assert frontier.prioritize_homepage(host) is False
    finally:
        frontier.close()


def test_completion_homepage_preempts_joined_backlog(tmp_path: Path) -> None:
    frontier = Frontier(tmp_path / "state.sqlite")
    try:
        for index in range(10):
            assert frontier.add(
                f"https://joined{index}.example.org/", depth=0,
                source="joined-candidate", priority=10,
            )
        assert frontier.prioritize_homepage("priority-reader.example.org")
        first = frontier.claim(1, max_pages_domain=1)
        assert first[0].url == "https://priority-reader.example.org/"
    finally:
        frontier.close()


def test_safe_same_origin_redirect_obeys_robots(tmp_path: Path) -> None:
    calls: list[str] = []
    host = "samehost.example.org"

    def fetch(url: str, _budget: object, **_kwargs: object) -> PageResponse:
        calls.append(url)
        if url.endswith("robots.txt"):
            return PageResponse(200, "text/plain", b"User-agent: *\\nDisallow: /private\\n")
        if url == f"https://{host}/":
            return PageResponse(302, "text/html", b"", "/private")
        raise AssertionError(f"unsafe fetch: {url}")

    crawler = WebCrawler(CrawlConfig(
        tmp_path / "robots.sqlite", max_browser=0, delay=0,
        max_requests=10, max_depth=0,
    ), fetch=fetch)
    try:
        assert crawler.frontier.add(f"https://{host}/", depth=0, source="catalog")
        crawler.run()
    finally:
        crawler.close()
    assert calls == [f"https://{host}/robots.txt", f"https://{host}/"]


def test_cross_origin_https_redirect_queues_new_robots_checked_host(tmp_path: Path) -> None:
    seen: list[str] = []
    old, new = "old.example.org", "www.reader.example.org"

    def fetch(url: str, _budget: object, **_kwargs: object) -> PageResponse:
        seen.append(url)
        if url.endswith("robots.txt"):
            return PageResponse(404, "text/plain", b"")
        if url == f"https://{old}/":
            return PageResponse(301, "text/html", b"", f"https://{new}/any/path")
        if url == f"https://{new}/":
            return PageResponse(200, "text/html", b"<title>Manga reader</title>")
        if url.endswith("/sitemap.xml"):
            return PageResponse(404, "text/plain", b"")
        raise AssertionError(f"unexpected fetch: {url}")

    crawler = WebCrawler(CrawlConfig(
        tmp_path / "redirect.sqlite", max_browser=0, delay=0, max_requests=18,
        max_depth=2,
    ), fetch=fetch)
    try:
        assert crawler.frontier.add(f"https://{old}/", depth=0, source="catalog")
        crawler.run()
    finally:
        crawler.close()
    assert f"https://{new}/robots.txt" in seen
    assert f"https://{new}/" in seen
    assert f"https://{new}/any/path" not in seen


def test_weak_leads_not_all_labeled_as_missing_second_catalog() -> None:
    row: dict[str, object] = {
        "status": "review", "review_eligible": False,
        "independent_families": 0, "onsite_content_pages": 0,
        "onsite_distinct_titles": 0, "eligibility_paths": [
            {"path_code": "independent-catalogs",
             "path_gaps": ["two_independent_catalog_families"]},
            {"path_code": "web-original",
             "path_gaps": ["topical_homepage", "two_verified_content_pages"]},
        ],
    }
    detail = candidate_decision(row)
    assert detail["closest_path"] is None
    assert detail["missing_evidence"] == ["no_independent_or_onsite_evidence"]


def test_denied_homepage_waits_for_backoff(tmp_path: Path) -> None:
    frontier = Frontier(tmp_path / "denied.sqlite")
    host = "blocked-reader.example.org"
    try:
        assert frontier.add(f"https://{host}/", depth=0, source="catalog")
        page = frontier.claim(1, max_pages_domain=3)[0]
        frontier.complete(page, "robots-access-denied")
        frontier.evidence(_content(host, 1))
        frontier.evidence(_content(host, 2))
        assert frontier.prioritize_homepage(host) is False
        assert frontier.prioritize_promising_homepages() == 0
        assert frontier.claim(1, max_pages_domain=3) == []
    finally:
        frontier.close()


def test_catalog_with_verified_pages_reaches_review_pr_without_homepage(tmp_path: Path) -> None:
    import json

    from ace_anime_discovery.propose_anime_sites import propose_sites

    host = "catalog-reader.example.org"
    evidence = [
        Evidence(host, "Manga Reader", "manga", (), "aidoku", None, "aidoku", "baseURL"),
        _content(host, 1), _content(host, 2),
    ]
    rows, approved = classify(evidence, set(), set(), set(), set())
    assert approved == set()
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(json.dumps(rows[0]) + "\n", encoding="utf-8")
    seeds = tmp_path / "seeds.txt"
    pro = tmp_path / "pro.txt"
    seeds.write_text("", encoding="utf-8")
    pro.write_text("", encoding="utf-8")
    selected = propose_sites(
        ledger, seeds, pro, tmp_path / "whitelist.txt",
        tmp_path / "exclusions.txt", tmp_path / "pr.md",
    )
    assert selected == [host]
    assert seeds.read_text(encoding="utf-8") == host + "\n"
    assert rows[0]["release_approved"] is False


def test_weak_forged_content_evidence_cannot_skip_homepage(tmp_path: Path) -> None:
    import json

    from ace_anime_discovery.propose_anime_sites import propose_sites

    host = "unverified-reader.example.org"
    evidence = [
        Evidence(host, "Manga Reader", "manga", (), "aidoku", None, "aidoku", "baseURL"),
        Evidence(
            host, "Manga Series 1", "manga", (), "site-internal-page",
            None, "web-lead", "structured-content-page",
            origin=f"https://{host}/series/1",
        ),
        Evidence(
            host, "Manga Series 2", "manga", (), "site-internal-page",
            None, "web-lead", "structured-content-page",
            origin=f"https://{host}/series/2",
        ),
    ]
    rows, approved = classify(evidence, set(), set(), set(), set())
    assert approved == set()
    # Classification may accept legacy records, but the review PR cannot
    # shortcut the homepage without structurally verified content records.
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(json.dumps(rows[0]) + "\n", encoding="utf-8")
    seeds = tmp_path / "seeds.txt"
    pro = tmp_path / "pro.txt"
    seeds.write_text("", encoding="utf-8")
    pro.write_text("", encoding="utf-8")
    selected = propose_sites(
        ledger, seeds, pro, tmp_path / "whitelist.txt",
        tmp_path / "exclusions.txt", tmp_path / "pr.md",
    )
    assert selected == []
