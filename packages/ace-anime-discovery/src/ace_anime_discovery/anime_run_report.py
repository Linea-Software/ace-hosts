"""One self-contained, privacy-conscious SQLite artifact per crawler run.

The report database is intentionally separate from the persistent crawl queue:
it can be downloaded, queried, archived or discarded without changing crawl
scheduling. Rows contain hostnames and bounded classifications, never URLs,
query strings, response bodies, authentication headers or tokens.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import cast


SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);
CREATE TABLE http_requests (
    id INTEGER PRIMARY KEY,
    hostname TEXT NOT NULL,
    phase TEXT NOT NULL,
    status_code INTEGER,
    error_code TEXT,
    latency_ms INTEGER NOT NULL,
    attempt INTEGER NOT NULL
);
CREATE INDEX http_requests_phase ON http_requests(phase, status_code, error_code);
CREATE TABLE page_events (
    id INTEGER PRIMARY KEY,
    hostname TEXT NOT NULL,
    source TEXT NOT NULL,
    page_type TEXT NOT NULL,
    outcome TEXT NOT NULL
);
CREATE INDEX page_events_outcome ON page_events(outcome);
CREATE TABLE candidates (
    hostname TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    review_eligible INTEGER NOT NULL,
    decision_code TEXT NOT NULL,
    data_json TEXT NOT NULL
);
CREATE INDEX candidates_decision ON candidates(decision_code);
CREATE TABLE missing_evidence (
    hostname TEXT NOT NULL,
    missing_code TEXT NOT NULL,
    PRIMARY KEY (hostname, missing_code)
);
CREATE TABLE source_attribution (
    hostname TEXT NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (hostname, source)
);
CREATE TABLE source_yield (
    source TEXT PRIMARY KEY,
    attempted_hosts INTEGER NOT NULL,
    candidate_hosts_in_ledger INTEGER NOT NULL,
    review_eligible_hosts INTEGER NOT NULL
);
"""


class RunReport:
    """Append events as work happens; finish later with candidate decisions."""

    def __init__(self, path: Path, run_id: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # This is a per-invocation artifact, not the durable frontier. Opening
        # a new run replaces the previous local report, never concatenates it.
        path.unlink(missing_ok=True)
        self.path: Path = path
        self.lock: threading.Lock = threading.Lock()
        self.connection: sqlite3.Connection = sqlite3.connect(
            path, check_same_thread=False,
        )
        self.connection.executescript(_SCHEMA)
        self.put_metadata("schema_version", SCHEMA_VERSION)
        self.put_metadata("run_id", run_id)
        self.put_metadata("started_at_unix", time.time())
        self.put_metadata("status", "running")

    def put_metadata(self, name: str, value: object) -> None:
        with self.lock, self.connection:
            self.connection.execute(
                "INSERT INTO metadata(key,value_json) VALUES(?,?) "
                + "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                (name, json.dumps(value, ensure_ascii=False, sort_keys=True)),
            )

    def record_http(self, hostname: str, phase: str, *,
                    status_code: int | None = None, error_code: str | None = None,
                    latency_ms: int, attempt: int) -> None:
        with self.lock, self.connection:
            self.connection.execute(
                "INSERT INTO http_requests(hostname,phase,status_code,error_code,"
                + "latency_ms,attempt) VALUES(?,?,?,?,?,?)",
                (hostname, phase, status_code, error_code, latency_ms, attempt),
            )

    def record_page(self, hostname: str, source: str,
                    page_type: str, outcome: str) -> None:
        with self.lock, self.connection:
            self.connection.execute(
                "INSERT INTO page_events(hostname,source,page_type,outcome) "
                + "VALUES(?,?,?,?)", (hostname, source, page_type, outcome),
            )

    def close(self) -> None:
        with self.lock:
            self.connection.close()


def finalize_run_report(path: Path, *,
                        run_id: str | None,
                        summary: dict[str, int],
                        report: dict[str, object],
                        rows: list[dict[str, object]],
                        decisions: list[dict[str, object]],
                        run_sources: dict[str, list[str]] | None,
                        status: str = "complete") -> None:
    """Enrich the existing artifact with all review decisions and metrics.

    If the crawler had an early failure the artifact remains available with
    the events already persisted; this function runs only after classification.
    """
    if not path.is_file():
        raise ValueError(f"missing run report database: {path}")
    with sqlite3.connect(path) as db:
        current_id = cast(
            tuple[str] | None, db.execute(
                "SELECT value_json FROM metadata WHERE key='run_id'"
            ).fetchone(),
        )
        if current_id is None or json.loads(current_id[0]) != run_id:
            raise ValueError("run report ID mismatch")
        db.execute("DELETE FROM candidates")
        db.execute("DELETE FROM missing_evidence")
        db.execute("DELETE FROM source_attribution")
        db.execute("DELETE FROM source_yield")
        decisions_by_host = {
            cast(str, row["hostname"]): row for row in decisions
        }
        for row in rows:
            host = row.get("hostname")
            if not isinstance(host, str):
                continue
            detail = decisions_by_host.get(host, {})
            # Deliberately omit origin URLs, query strings and untrusted page
            # text. Keep the structured signals needed to study classifications.
            allow = (
                "hostname", "status", "review_eligible", "confidence",
                "verification_method", "independent_families", "joined",
                "onsite_content_pages", "onsite_distinct_titles", "categories",
                "languages", "repositories", "evidence", "decision_reason",
            )
            normalized = {key: row[key] for key in allow if key in row}
            db.execute(
                "INSERT INTO candidates(hostname,status,review_eligible,"
                + "decision_code,data_json) VALUES(?,?,?,?,?)",
                (host, str(row.get("status", "")),
                 int(detail.get("review_eligible") is True),
                 str(detail.get("code", "unknown")),
                 json.dumps(normalized, ensure_ascii=False, sort_keys=True)),
            )
            gaps = detail.get("missing_evidence", [])
            if isinstance(gaps, list):
                for gap in cast(list[object], gaps):
                    if isinstance(gap, str):
                        db.execute(
                            "INSERT OR IGNORE INTO missing_evidence"
                            + "(hostname,missing_code) VALUES(?,?)",
                            (host, gap),
                        )
        for host, sources in (run_sources or {}).items():
            for source in set(sources):
                db.execute(
                    "INSERT OR IGNORE INTO source_attribution(hostname,source) "
                    + "VALUES(?,?)", (host, source),
                )
        raw_diagnostics = report.get("diagnostics")
        diagnostics = (cast(dict[str, object], raw_diagnostics)
                       if isinstance(raw_diagnostics, dict) else {})
        raw_yields = diagnostics.get("source_yield_current_run")
        yields = (cast(dict[str, object], raw_yields)
                  if isinstance(raw_yields, dict) else {})
        for source, values in yields.items():
            if not isinstance(values, dict):
                continue
            metrics = cast(dict[str, object], values)
            attempted = metrics.get("attempted_hosts")
            in_ledger = metrics.get("candidate_hosts_in_ledger")
            eligible = metrics.get("review_eligible_hosts")
            db.execute(
                "INSERT INTO source_yield(source,attempted_hosts,"
                + "candidate_hosts_in_ledger,review_eligible_hosts)"
                + " VALUES(?,?,?,?)",
                (source, attempted if type(attempted) is int else 0,
                 in_ledger if type(in_ledger) is int else 0,
                 eligible if type(eligible) is int else 0),
            )
        metadata: dict[str, object] = {
            "summary": summary,
            "current_run": report.get("current_run", {}),
            "lifetime_state": report.get("lifetime_state", {}),
            "candidate_snapshot": diagnostics,
            "report": report,
            "status": status,
            "finished_at_unix": time.time(),
        }
        for key, value in metadata.items():
            db.execute(
                "INSERT INTO metadata(key,value_json) VALUES(?,?) "
                + "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                (key, json.dumps(value, ensure_ascii=False, sort_keys=True)),
            )
