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
