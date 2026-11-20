"""Offline verification of the single, self-contained per-run SQLite artifact."""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import cast

import pytest

from ace_anime_discovery.anime_crawler import (
    CrawlConfig, PageResponse, WebCrawler,
)
from ace_anime_discovery.anime_diagnostics import (
    candidate_decision, summarize_candidates,
)
from ace_anime_discovery.anime_discovery import (
    Evidence, MIN_JOINED_DOMAINS, build_discovery,
)
from ace_anime_discovery.anime_run_report import finalize_run_report


def _meta(db: sqlite3.Connection, key: str) -> object:
    row = cast(
        tuple[str] | None,
        db.execute("SELECT value_json FROM metadata WHERE key=?", (key,)).fetchone(),
    )
    assert row is not None
    return cast(object, json.loads(row[0]))


def _run(tmp_path: Path) -> tuple[dict[str, object], Path, Path]:
    state = tmp_path / "state.sqlite"
    report = tmp_path / "artifacts" / "crawl-report.sqlite"
    host = "unlisted-reader.example.org"

    def fetch(url: str, _budget: object, **_kwargs: object) -> PageResponse:
        if url.endswith("/robots.txt"):
            return PageResponse(404, "text/plain", b"Not found")
        if url.endswith("/sitemap.xml"):
            return PageResponse(404, "text/plain", b"Not found")
        if url == f"https://{host}/":
            return PageResponse(
                200, "text/html",
                b'<title>Manga Reader</title>'
                + b'<meta name="description" content="Read manga">'
                + b'<a href="/series/one-piece?chapter=867">Manga One Piece</a>'
                + b'<a href="/series/naruto">Manga Naruto</a>',
            )
        if "/series/" in url:
            return PageResponse(
                200, "text/html",
                b"<title>Real Manga Chapter 100</title>",
            )
        raise AssertionError(f"unplanned request to {url}")

    web = WebCrawler(
        CrawlConfig(
            state, report_path=report, delay=0, max_requests=50,
            max_pages_domain=3, max_depth=2, concurrency=2, max_browser=0,
        ), fetch=fetch,
    )
    try:
        assert web.frontier.add(
            f"https://{host}/", depth=0, source="brave-search:en",
        )
        stats = web.run()
    finally:
        web.close()
    return stats, state, report


def test_one_run_report_has_all_events_no_sensitive_urls(tmp_path: Path) -> None:
    stats, state, report = _run(tmp_path)
    assert report.is_file()
    assert state.is_file()
    assert report != state
    current = cast(dict[str, object], stats["current_run"])
    assert current["run_id"]
    with sqlite3.connect(report) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        tables = {
            v[0] for v in cast(list[tuple[str]], db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall())
        }
        assert {
            "metadata", "http_requests", "page_events", "candidates",
            "missing_evidence", "source_attribution", "source_yield",
        } <= tables
        assert db.execute("SELECT COUNT(*) FROM page_events").fetchone()[0] == 3
        assert db.execute(
            "SELECT COUNT(*) FROM http_requests WHERE phase='robots'"
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM http_requests WHERE phase='sitemap'"
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM http_requests WHERE phase='page'"
        ).fetchone()[0] == 3
        assert {
            r[0] for r in cast(list[tuple[str]], db.execute(
                "SELECT DISTINCT source FROM page_events"
            ).fetchall())
        } == {"brave-search:en", "internal"}
        assert _meta(db, "status") == "crawl_complete"
        assert _meta(db, "run_id") == current["run_id"]
        assert _meta(db, "current_run") == current
    # Paths and query strings stay in the private crawler state, not report.
    assert b"chapter=867" not in report.read_bytes()
    assert b"/series/one-piece" not in report.read_bytes()
    assert b"password" not in report.read_bytes()


def test_finalize_report_includes_classification_and_source_yield(tmp_path: Path) -> None:
    stats, _state, report = _run(tmp_path)
    host = "unlisted-reader.example.org"
    row: dict[str, object] = {
        "hostname": host,
        "status": "website-verified",
        "review_eligible": True,
        "verification_method": "multi-page-website",
        "independent_families": 0,
        "joined": False,
        "onsite_content_pages": 2,
        "onsite_distinct_titles": 2,
        "categories": ["manga"],
        "languages": ["en"],
        "repositories": ["site-homepage"],
        "evidence": [
            "site-homepage:html-title-description",
            "site-internal-page:structured-content-page",
        ],
        "confidence": 75,
        "discovery_sources": ["https://unlisted-reader.example.org/private?token=secret"],
    }
    diagnostics, decisions = summarize_candidates(
        [row], run_host_sources={host: ["brave-search:en"]},
    )
    assert candidate_decision(row)["code"] == "ready_for_review"
    current = cast(dict[str, object], stats["current_run"])
    report_data: dict[str, object] = {
        "current_run": current,
        "lifetime_state": {"urls_discovered": 3},
        "diagnostics": diagnostics,
    }
    finalize_run_report(
        report,
        run_id=cast(str, current["run_id"]),
        summary={"review_candidates": 1},
        report=report_data,
        rows=[row],
        decisions=decisions,
        run_sources={host: ["brave-search:en"]},
    )
    with sqlite3.connect(report) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute(
            "SELECT hostname,decision_code,review_eligible FROM candidates"
        ).fetchone() == (host, "ready_for_review", 1)
        assert db.execute(
            "SELECT source,attempted_hosts,review_eligible_hosts FROM source_yield"
        ).fetchone() == ("brave-search:en", 1, 1)
        assert db.execute(
            "SELECT hostname,source FROM source_attribution"
        ).fetchone() == (host, "brave-search:en")
        assert _meta(db, "summary") == {"review_candidates": 1}
        assert _meta(db, "status") == "complete"
    assert b"token=secret" not in report.read_bytes()
    assert b"/private" not in report.read_bytes()
    with pytest.raises(ValueError, match="run report ID mismatch"):
        finalize_run_report(
            report, run_id="different", summary={}, report=report_data,
            rows=[], decisions=[], run_sources=None,
        )


def test_repeated_run_creates_new_report_but_preserves_frontier(tmp_path: Path) -> None:
    old, state, report = _run(tmp_path)
    old_id = cast(dict[str, object], old["current_run"])["run_id"]
    next_crawler = WebCrawler(
        CrawlConfig(state, report_path=report, max_browser=0, delay=0),
    )
    try:
        assert next_crawler.frontier.seen_hosts() == {"unlisted-reader.example.org"}
        with sqlite3.connect(report) as db:
            assert db.execute("SELECT COUNT(*) FROM page_events").fetchone()[0] == 0
        second = next_crawler.run()
        assert second["urls_discovered"] == old["urls_discovered"]
        second_run = cast(dict[str, object], second["current_run"])
        assert second_run["run_id"] != old_id
        funnel = cast(dict[str, object], second_run["funnel"])
        assert funnel["attempted_hosts"] == 0
    finally:
        next_crawler.close()


def test_report_is_available_before_crawl_completion(tmp_path: Path) -> None:
    path = tmp_path / "run.sqlite"
    web = WebCrawler(
        CrawlConfig(tmp_path / "state.sqlite", report_path=path,
                    max_browser=0, delay=0),
    )
    try:
        assert path.is_file()
        with sqlite3.connect(path) as db:
            assert _meta(db, "status") == "running"
    finally:
        web.close()


def test_build_discovery_populates_the_same_sqlite_artifact(tmp_path: Path) -> None:
    stats, _state, report_path = _run(tmp_path)
    joined = tmp_path / "joined.txt"
    joined.write_text(
        "".join(f"joined-site-{i}.org\n" for i in range(MIN_JOINED_DOMAINS)),
        encoding="utf-8",
    )
    pro = tmp_path / "pro.txt"
    pro.write_text(
        "".join(f"curated-site-{i}.org\n" for i in range(101)),
        encoding="utf-8",
    )
    host = "unlisted-reader.example.org"
    records = [
        Evidence(host, "Manga Reader", "manga", ("en",), repository, "id",
                 family, "baseURL")
        for family, repository in (("aidoku", "aidoku"), ("mangayomi", "mangayomi"))
    ]
    summary = build_discovery(
        records, joined_file=joined, pro_file=pro,
        approved_file=tmp_path / "approved.txt",
        whitelist_file=tmp_path / "whitelist.txt",
        exceptions_file=tmp_path / "exceptions.txt",
        output_dir=report_path.parent, checked_at="2026-11-20",
        crawl_stats=stats,
    )
    assert summary["review_candidates"] == 1
    with sqlite3.connect(report_path) as db:
        assert _meta(db, "status") == "complete"
        assert _meta(db, "summary") == summary
        assert db.execute(
            "SELECT decision_code FROM candidates WHERE hostname=?", (host,)
        ).fetchone()[0] == "ready_for_review"
        assert db.execute(
            "SELECT COUNT(*) FROM source_yield"
        ).fetchone()[0] >= 1


def test_console_info_is_progress_not_per_page(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="ace-hosts.anime-crawler")
    _run(tmp_path)
    assert "crawl started:" in caplog.text
    assert "crawl finished:" in caplog.text
    assert "crawl visiting:" not in caplog.text
    assert "crawl skipped:" not in caplog.text
