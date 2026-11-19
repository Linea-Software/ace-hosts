"""Offline regression tests for incremental discovery and fair scheduling."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from ace_anime_discovery.anime_crawler import CrawlConfig, Frontier, WebCrawler
from ace_anime_discovery.anime_discovery import Evidence
from ace_anime_discovery.anime_search import search_queries


def test_priority_queue_does_not_starve_other_hosts(tmp_path: Path) -> None:
    frontier = Frontier(tmp_path / "frontier.sqlite")
    try:
        for i in range(450):
            assert frontier.add(
                f"https://priority.example.org/page/{i}", depth=1,
                source="index", priority=90
            )
        for i in range(7):
            assert frontier.add(
                f"https://other{i}.example.org/", depth=0,
                source="search", priority=10
            )
        first = frontier.claim(12, max_pages_domain=1)
        assert len(first) == 8
        assert len({page.url.split("/")[2] for page in first}) == 8
        for page in first:
            frontier.complete(page)
        assert frontier.claim(12, max_pages_domain=1) == []
    finally:
        frontier.close()


def test_successful_urls_are_refreshed_only_when_due(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite"
    frontier = Frontier(path)
    assert frontier.add("https://anime.example.org/", depth=0, source="catalog")
    page = frontier.claim(1, 3)[0]
    frontier.complete(page)
    assert frontier.claim(1, 3) == []
    frontier.close()

    resumed = Frontier(path)
    assert resumed.claim(1, 3) == []
    resumed.conn.execute(
        "UPDATE frontier SET next_check=1 WHERE url=?", (page.url,)
    )
    assert resumed.claim(1, 3) == [page]
    resumed.complete(page, "http-503")
    assert resumed.claim(1, 3) == []
    resumed.close()


def test_migrate_legacy_state_without_dropping_urls(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite"
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE frontier (
          url TEXT PRIMARY KEY, host TEXT NOT NULL, depth INTEGER NOT NULL,
          priority INTEGER NOT NULL, source TEXT NOT NULL,
          kind TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
          error TEXT NOT NULL DEFAULT ''
        );
        INSERT INTO frontier VALUES(
          'https://old.example.org/', 'old.example.org', 0,
          50, 'catalog', 'lead', 'done', ''
        );
    """)
    db.close()
    frontier = Frontier(path)
    try:
        assert frontier.claim(1, 3)[0].url == "https://old.example.org/"
        assert frontier.search_cursor() == 0
        frontier.advance_search_cursor(24)
        assert frontier.search_cursor() == 24
    finally:
        frontier.close()


def test_seed_scheduler_advances_across_runs(tmp_path: Path) -> None:
    path = tmp_path / "seeds.sqlite"
    sources = [
        Evidence(f"reader{i}.example.org", None, "manga", ("en",), "catalog",
                 None, "reader", "baseUrl") for i in range(10)
    ]
    config = CrawlConfig(path, max_seed_hosts=3, max_browser=0)
    first = WebCrawler(config)
    try:
        first.seed(sources, with_public_indexes=False)
        assert len(first.frontier.seen_hosts()) == 3
    finally:
        first.close()
    second = WebCrawler(config)
    try:
        second.seed(sources, with_public_indexes=False)
        assert len(second.frontier.seen_hosts()) == 6
    finally:
        second.close()


def test_search_cursor_rotates_languages_and_topics() -> None:
    initial = list(search_queries(max_queries=16))
    next_batch = list(search_queries(max_queries=16, start_index=16))
    assert initial != next_batch


def test_rechecking_homepage_replaces_stale_evidence(tmp_path: Path) -> None:
    frontier = Frontier(tmp_path / "evidence.sqlite")
    try:
        evidence = Evidence("manga.example.org", "Manga", "manga", (),
                            "site-homepage", None, "web-lead",
                            "html-title-description", "https://manga.example.org/")
        frontier.evidence(evidence)
        assert len(frontier.records()) == 1
        frontier.clear_homepage_evidence("manga.example.org")
        assert frontier.records() == []
    finally:
        frontier.close()


def test_joined_backlog_rotates_and_is_not_published(tmp_path: Path) -> None:
    from ace_anime_discovery.anime_discovery import classify

    hosts = [f"joined{i}.example.org" for i in range(6)]
    path = tmp_path / "joined.sqlite"
    config = CrawlConfig(path, max_seed_hosts=0, max_joined_hosts=2,
                         max_browser=0)
    first = WebCrawler(config)
    try:
        first.seed_joined(hosts)
        assert first.frontier.joined_cursor() == 2
        assert len(first.frontier.seen_hosts()) == 2
        evidence = first.frontier.records()
        assert all(row.repository == "joined-candidate" for row in evidence)
        rows, approved = classify(evidence, set(hosts), set(), set(), set())
        assert len(rows) == 2
        assert approved == set()
        assert all(row["review_eligible"] is False for row in rows)
    finally:
        first.close()

    second = WebCrawler(config)
    try:
        second.seed_joined(hosts)
        assert second.frontier.joined_cursor() == 4
        assert len(second.frontier.seen_hosts()) == 4
    finally:
        second.close()


def test_joined_site_homepage_is_not_independent_verification(tmp_path: Path) -> None:
    from ace_anime_discovery.anime_discovery import classify

    crawl = WebCrawler(CrawlConfig(tmp_path / "verified.sqlite",
                                   max_joined_hosts=1, max_browser=0))
    try:
        crawl.seed_joined(["mangareader.example.org"])
        crawl.frontier.evidence(Evidence(
            "mangareader.example.org", "Manga Reader", "manga", ("it",),
            "site-homepage", None, "web-lead", "html-title-description",
            "https://mangareader.example.org/"
        ))
        ledger, approved = classify(crawl.frontier.records(),
                                    {"mangareader.example.org"}, set(), set(), set())
        assert ledger[0]["status"] == "review"
        assert ledger[0]["verification_method"] == "website-observed"
        assert ledger[0]["review_eligible"] is False
        assert approved == set()
    finally:
        crawl.close()


def test_joined_website_only_candidate_cannot_reach_pr(tmp_path: Path) -> None:
    import json

    from ace_anime_discovery.anime_discovery import classify
    from ace_anime_discovery.propose_anime_sites import propose_sites

    host = "site.example.org"
    evidence = [
        Evidence(host, None, "unknown", (), "joined-candidate", None,
                 "web-lead", "domain", "newedgex-joined"),
        Evidence(host, "Site", "manga", (), "site-homepage", None,
                 "web-lead", "html-title-description", f"https://{host}/"),
    ]
    ledger, approved = classify(evidence, {host}, set(), set(), set())
    assert approved == set()
    path = tmp_path / "ledger.jsonl"
    path.write_text(json.dumps(ledger[0]) + "\n", encoding="utf-8")
    seeds = tmp_path / "seeds.txt"
    seeds.write_text("", encoding="utf-8")
    pro = tmp_path / "pro.txt"
    pro.write_text("", encoding="utf-8")
    accepted = propose_sites(path, seeds, pro, tmp_path / "whitelist.txt",
                             tmp_path / "exclusions.txt", tmp_path / "pr.md")
    assert accepted == []
    assert seeds.read_text(encoding="utf-8") == ""


def test_joined_homepage_requires_more_than_incidental_blog_reference(tmp_path: Path) -> None:
    from ace_anime_discovery.anime_crawler import PageResponse

    def get(url: str, _budget: object, **_kwargs: object) -> PageResponse:
        if url.endswith("/robots.txt"):
            return PageResponse(200, "text/plain", b"User-agent: *\nAllow: /\n")
        if url.endswith("/sitemap.xml"):
            return PageResponse(404, "text/plain", b"")
        return PageResponse(200, "text/html", (
            b"<title>General news</title><body>Today we discussed manga"
            b" in an article.</body>"
        ))

    crawl = WebCrawler(CrawlConfig(tmp_path / "safe.sqlite", max_browser=0,
                                   delay=0, max_joined_hosts=1), fetch=get)
    try:
        crawl.seed_joined(["news.example.org"])
        result = crawl.run()
        assert result["websites_verified"] == 0
        assert not any(row.repository == "site-homepage"
                       for row in crawl.frontier.records())
    finally:
        crawl.close()


def test_interrupted_evidence_write_requeues_page(tmp_path: Path) -> None:
    import pytest
    from typing import cast
    from ace_anime_discovery.anime_crawler import InspectedPage

    path = tmp_path / "interrupted.sqlite"
    crawler = WebCrawler(CrawlConfig(path, max_browser=0))
    assert crawler.frontier.add("https://manga.example.org/", depth=0,
                                source="catalog")
    page = crawler.frontier.claim(1, 3)[0]
    original = crawler.frontier.evidence

    def interrupted(entry: Evidence) -> None:
        del entry
        raise OSError("filesystem or database write failed")

    crawler.frontier.evidence = interrupted  # type: ignore[method-assign]
    with pytest.raises(OSError):
        crawler._record(InspectedPage(page, "Manga", ("manga",), (), (), ()), None)  # pyright: ignore[reportPrivateUsage]
    crawler.frontier.evidence = original  # type: ignore[method-assign]
    state = cast(tuple[str] | None, crawler.frontier.conn.execute(
        "SELECT status FROM frontier WHERE url=?", (page.url,)
    ).fetchone())
    assert state == ("inflight",)
    crawler.close()

    resumed = Frontier(path)
    try:
        assert resumed.claim(1, 3) == [page]
    finally:
        resumed.close()


def test_joined_input_change_resets_cursor_and_keeps_seen_hosts(tmp_path: Path) -> None:
    from ace_anime_discovery.anime_crawler import fingerprint_file

    source = tmp_path / "joined.txt"
    source.write_text("a.example.org\nb.example.org\nc.example.org\n", encoding="utf-8")
    fingerprint_a = fingerprint_file(source)
    frontier_path = tmp_path / "rotating.sqlite"
    config = CrawlConfig(frontier_path, max_joined_hosts=2, max_browser=0)
    first = WebCrawler(config)
    try:
        first.seed_joined(source.read_text(encoding="utf-8").splitlines(),
                          fingerprint=fingerprint_a)
        assert first.frontier.joined_cursor() == 2
        assert first.frontier.seen_hosts() == {"a.example.org", "b.example.org"}
    finally:
        first.close()
    source.write_text("new.example.org\na.example.org\nb.example.org\nc.example.org\n",
                      encoding="utf-8")
    second = WebCrawler(config)
    try:
        second.seed_joined(source.read_text(encoding="utf-8").splitlines(),
                           fingerprint=fingerprint_file(source))
        assert second.frontier.seen_hosts() == {
            "new.example.org", "a.example.org", "b.example.org", "c.example.org",
        }
        assert second.frontier.joined_cursor() == 4
    finally:
        second.close()


def test_robots_rate_policies_override_short_default_delay(tmp_path: Path) -> None:
    from ace_anime_discovery.anime_crawler import PageResponse

    for index, policy in enumerate(("Crawl-delay: 120", "Request-rate: 1/100")):
        def fetch(url: str, _budget: object, **_kw: object) -> PageResponse:
            assert url.endswith("/robots.txt")
            return PageResponse(200, "text/plain",
                                f"User-agent: *\n{policy}\nAllow: /".encode())
        web = WebCrawler(CrawlConfig(tmp_path / f"policy-{index}.sqlite",
                                     max_browser=0, delay=0.6), fetch=fetch)
        try:
            assert web._allowed("https://series.example.org/")  # pyright: ignore[reportPrivateUsage]
            assert web._host_delay["series.example.org"] >= (120 if index == 0 else 100)  # pyright: ignore[reportPrivateUsage]
        finally:
            web.close()


def test_failed_robots_fetch_is_cached_for_whole_run(tmp_path: Path) -> None:
    from ace_anime_discovery.anime_crawler import PageResponse

    seen: list[str] = []

    def fetch(url: str, _budget: object, **_kw: object) -> PageResponse:
        seen.append(url)
        return PageResponse(429, "text/plain", b"")

    web = WebCrawler(CrawlConfig(tmp_path / "limited.sqlite",
                                 max_browser=0, delay=0), fetch=fetch)
    try:
        assert not web._allowed("https://limited.example.org/a")  # pyright: ignore[reportPrivateUsage]
        assert not web._allowed("https://limited.example.org/b")  # pyright: ignore[reportPrivateUsage]
        assert seen == ["https://limited.example.org/robots.txt"]
        assert web._robots_failures["limited.example.org"] == "robots-rate-limited"  # pyright: ignore[reportPrivateUsage]
    finally:
        web.close()


def test_article_page_cannot_verify_generic_news_homepage(tmp_path: Path) -> None:
    from ace_anime_discovery.anime_crawler import PageResponse
    from ace_anime_discovery.anime_discovery import classify

    host = "news.example.org"

    def fetch(url: str, _budget: object, **_kw: object) -> PageResponse:
        if url.endswith("/robots.txt"):
            return PageResponse(200, "text/plain", b"User-agent: *\nAllow: /\n")
        if url.endswith("/sitemap.xml"):
            return PageResponse(200, "application/xml", (
                f"<urlset><url><loc>https://{host}/manga-article</loc></url></urlset>"
            ).encode())
        if url.endswith("/manga-article"):
            return PageResponse(200, "text/html",
                                b"<title>How manga changed modern media</title>")
        return PageResponse(200, "text/html", b"<title>General technology news</title>")

    web = WebCrawler(CrawlConfig(tmp_path / "news.sqlite", max_browser=0,
                                 delay=0, max_depth=2), fetch=fetch)
    try:
        web.seed_joined([host])
        web.run()
        evidence = web.frontier.records()
        assert any(item.repository == "site-internal-page" for item in evidence)
        assert not any(item.repository == "site-homepage" for item in evidence)
        ledger, approved = classify(evidence, {host}, set(), set(), set())
        assert ledger[0]["review_eligible"] is False
        assert approved == set()
    finally:
        web.close()


def test_root_429_invalidates_previous_homepage_evidence(tmp_path: Path) -> None:
    from ace_anime_discovery.anime_crawler import PageResponse
    from ace_anime_discovery.anime_discovery import classify

    host = "site.example.org"

    def fetch(url: str, _budget: object, **_kw: object) -> PageResponse:
        assert url.endswith("/robots.txt")
        return PageResponse(429, "text/plain", b"")

    web = WebCrawler(CrawlConfig(tmp_path / "denied.sqlite", max_browser=0,
                                 delay=0), fetch=fetch)
    try:
        web.seed_joined([host])
        web.frontier.evidence(Evidence(host, "Manga", "manga", (),
                                      "site-homepage", None, "web-lead",
                                      "html-title-description", f"https://{host}/"))
        assert any(e.repository == "site-homepage" for e in web.frontier.records())
        web.run()
        records = web.frontier.records()
        assert not any(e.repository == "site-homepage" for e in records)
        ledger, approved = classify(records, {host}, set(), set(), set())
        assert ledger[0]["review_eligible"] is False
        assert approved == set()
    finally:
        web.close()


def test_due_rechecks_are_not_starved_by_new_hosts(tmp_path: Path) -> None:
    frontier = Frontier(tmp_path / "fair-rechecks.sqlite")
    try:
        due_host = "old.example.org"
        assert frontier.add(f"https://{due_host}/", depth=0,
                            source="catalog", priority=20)
        old = frontier.claim(1, 3)[0]
        frontier.complete(old)
        frontier.conn.execute(
            "UPDATE frontier SET next_check=1 WHERE url=?", (old.url,)
        )
        for i in range(15):
            assert frontier.add(f"https://new{i}.example.org/", depth=0,
                                source="search", priority=80)
        selected = [frontier.claim(1, 3)[0] for _ in range(4)]
        assert old in selected
        for page in selected:
            frontier.complete(page)
    finally:
        frontier.close()


def test_unicode_query_is_safe_http_request_target() -> None:
    from ace_anime_discovery.anime_crawler import normalize_url

    url = normalize_url(
        "https://reader.example.org/search?chapter=漫画&lang=ja&utm_source=ignored"
    )
    assert url == (
        "https://reader.example.org/search?chapter=%E6%BC%AB%E7%94%BB&lang=ja"
    )
    assert url.encode("ascii")


def test_legacy_evidence_with_unknown_age_is_not_fresh(tmp_path: Path) -> None:
    db = tmp_path / "old-evidence.sqlite"
    with sqlite3.connect(db) as conn:
        conn.executescript("""
            CREATE TABLE evidence(
                host TEXT NOT NULL, category TEXT NOT NULL, repository TEXT NOT NULL,
                field TEXT NOT NULL, origin TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(host,category,repository,field,origin)
            );
        """)
        conn.execute(
            "INSERT INTO evidence VALUES(?,?,?,?,?,?)",
            ("old.example.org", "manga", "site-homepage", "html-title-description",
             "https://old.example.org/", "{}"),
        )
    migrated = Frontier(db)
    try:
        assert migrated.records() == []
        migrated.evidence(Evidence(
            "old.example.org", "Manga", "manga", (), "site-homepage",
            None, "web-lead", "html-title-description", "https://old.example.org/"
        ))
        assert len(migrated.records()) == 1
    finally:
        migrated.close()
