"""One self-contained, privacy-conscious SQLite artifact per crawler run.

The report database is intentionally separate from the persistent crawl queue:
it can be downloaded, queried, archived or discarded without changing crawl
scheduling. Rows contain hostnames and bounded classifications, never URLs,
query strings, response bodies, authentication headers or tokens.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import cast


SCHEMA_VERSION = 2

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
    failure_code TEXT,
    latency_ms INTEGER NOT NULL,
    attempt INTEGER NOT NULL
);
CREATE INDEX http_requests_phase ON http_requests(phase, status_code, error_code);
CREATE INDEX http_requests_failure ON http_requests(failure_code);
CREATE TABLE page_events (
    id INTEGER PRIMARY KEY,
    hostname TEXT NOT NULL,
    source TEXT NOT NULL,
    page_type TEXT NOT NULL,
    outcome TEXT NOT NULL,
    reason_codes_json TEXT NOT NULL DEFAULT '[]',
    positive_signals_json TEXT NOT NULL DEFAULT '[]',
    negative_signals_json TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX page_events_outcome ON page_events(outcome);
CREATE INDEX page_events_hostname ON page_events(hostname, page_type);
CREATE TABLE candidates (
    hostname TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    review_eligible INTEGER NOT NULL,
    decision_code TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    qualification_path TEXT NOT NULL DEFAULT '',
    data_json TEXT NOT NULL
);
CREATE INDEX candidates_decision ON candidates(decision_code);
CREATE TABLE missing_evidence (
    hostname TEXT NOT NULL,
    missing_code TEXT NOT NULL,
    reason_code TEXT NOT NULL,
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
    review_eligible_hosts INTEGER NOT NULL,
    newly_verified_hosts INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE eligibility_paths (
    hostname TEXT NOT NULL REFERENCES candidates(hostname),
    path_code TEXT NOT NULL,
    qualified INTEGER NOT NULL CHECK (qualified IN (0,1)),
    PRIMARY KEY (hostname, path_code)
);
CREATE INDEX eligibility_paths_status ON eligibility_paths(path_code, qualified);
CREATE TABLE eligibility_checks (
    hostname TEXT NOT NULL,
    path_code TEXT NOT NULL,
    check_code TEXT NOT NULL,
    passed INTEGER NOT NULL CHECK (passed IN (0,1)),
    required INTEGER NOT NULL CHECK (required IN (0,1)),
    PRIMARY KEY (hostname, path_code, check_code),
    FOREIGN KEY (hostname, path_code) REFERENCES eligibility_paths(hostname, path_code)
);
CREATE INDEX eligibility_checks_failed ON eligibility_checks(check_code, passed);
CREATE TABLE run_metrics (
    scope TEXT NOT NULL,
    metric TEXT NOT NULL,
    value INTEGER NOT NULL,
    PRIMARY KEY (scope, metric)
);
"""

# All newly introduced codes use a stable suffix for downstream queries.
_SIGNAL_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}_V1$")


def _safe_codes(codes: tuple[str, ...]) -> str:
    """Store bounded reason identifiers, never arbitrary page content."""
    return json.dumps(sorted({code for code in codes[:24] if _SIGNAL_CODE.fullmatch(code)}))


def http_failure_code(phase: str, status: int | None, error: str | None) -> str | None:
    """Normalize operational failures into versioned, bounded categories."""
    if error:
        name = error.lower()
        if "timeout" in name:
            return "HTTP_TIMEOUT_V1"
        if "ssl" in name or "tls" in name or "certificate" in name:
            return "HTTP_TLS_V1"
        if "gaierror" in name or "dns" in name:
            return "HTTP_DNS_V1"
        if "connection" in name or "socket" in name or "oserror" in name:
            return "HTTP_NETWORK_V1"
        return "HTTP_EXCEPTION_V1"
    if status is None:
        return None
    if status == 429:
        return "HTTP_RATE_LIMIT_V1"
    if status in (401, 403):
        return "ROBOTS_ACCESS_DENIED_V1" if phase == "robots" else "HTTP_ACCESS_DENIED_V1"
    if status >= 500:
        return "HTTP_SERVER_ERROR_V1"
    if 300 <= status < 400:
        return "HTTP_REDIRECT_V1"
    if 400 <= status < 500 and not (phase == "robots" and status in (404, 410)):
        return "HTTP_CLIENT_ERROR_V1"
    return None


def page_outcome_code(outcome: str) -> str:
    """Group page outcomes without persisting untrusted text or paths."""
    if outcome == "content_verified":
        return "PAGE_CONTENT_VERIFIED_V1"
    if outcome == "content_checked":
        return "PAGE_CONTENT_UNVERIFIED_V1"
    if outcome == "homepage_topical":
        return "PAGE_HOMEPAGE_TOPICAL_V1"
    if outcome == "homepage_unverified":
        return "PAGE_HOMEPAGE_UNVERIFIED_V1"
    if outcome.startswith("robots"):
        return "PAGE_ROBOTS_FAILURE_V1"
    if outcome in {"access denied", "sensitive-url"}:
        return "PAGE_ACCESS_DENIED_V1"
    if outcome == "budget-exhausted":
        return "PAGE_BUDGET_EXHAUSTED_V1"
    if outcome.startswith("http-"):
        return "PAGE_HTTP_FAILURE_V1"
    return "PAGE_OTHER_V1"


_CANDIDATE_CODES: dict[str, str] = {
    "ready_for_review": "CANDIDATE_REVIEW_READY_V1",
    "already_in_pro": "CANDIDATE_IN_PRO_V1",
    "already_approved": "CANDIDATE_ALREADY_APPROVED_V1",
    "excluded_by_maintainer_policy": "CANDIDATE_EXCLUDED_V1",
    "insufficient_verification": "CANDIDATE_EVIDENCE_MISSING_V1",
}


def _missing_reason_code(value: str) -> str:
    if re.fullmatch(r"[a-z][a-z0-9_]{0,55}", value):
        return "CHECK_" + value.upper() + "_V1"
    return "CHECK_UNCLASSIFIED_V1"


def _fallback_eligibility_checks(row: dict[str, object]) -> dict[str, dict[str, bool]]:
    """Legacy ledger fallback for reports created without structured pathways."""
    families = row.get("independent_families")
    count = families if type(families) is int else 0
    evidence = row.get("evidence")
    markers: set[str] = ({item for item in cast(list[object], evidence)
                          if isinstance(item, str)} if isinstance(evidence, list) else set())
    repos = row.get("repositories")
    repositories: set[str] = ({item for item in cast(list[object], repos)
                               if isinstance(item, str)} if isinstance(repos, list) else set())
    homepage = any(item.startswith("site-homepage:") for item in markers)
    joined = row.get("joined") is True
    pages_value = row.get("onsite_content_pages")
    titles_value = row.get("onsite_distinct_titles")
    pages = type(pages_value) is int and pages_value >= 2
    titles = type(titles_value) is int and titles_value >= 2
    web_source = bool(repositories.intersection({
        "brave-search", "web-link", "site-outbound", "web-directory", "joined-candidate",
    }))
    return {
        "CATALOG_INDEPENDENT_V1": {
            "CATALOG_FAMILIES_TWO_V1": count >= 2,
        },
        "CATALOG_JOINED_V1": {
            "CATALOG_FAMILIES_ONE_V1": count >= 1,
            "HOMEPAGE_TOPICAL_V1": homepage,
            "JOINED_MEMBERSHIP_V1": joined,
        },
        "CATALOG_SITE_V1": {
            "CATALOG_FAMILIES_ONE_V1": count >= 1,
            "HOMEPAGE_TOPICAL_V1": homepage,
            "CONTENT_PAGES_TWO_V1": pages,
            "DISTINCT_TITLES_TWO_V1": titles,
        },
        "WEB_ORIGINAL_V1": {
            "NO_CATALOG_FAMILIES_V1": count == 0,
            "INDEPENDENT_WEB_LEAD_V1": web_source,
            "HOMEPAGE_TOPICAL_V1": homepage,
            "CONTENT_PAGES_TWO_V1": pages,
            "DISTINCT_TITLES_TWO_V1": titles,
        },
    }


def _eligibility_checks(row: dict[str, object]) -> dict[str, dict[str, bool]]:
    """Prefer the exact evaluated path requirements from classification."""
    evaluated = row.get("eligibility_paths")
    if not isinstance(evaluated, list):
        return _fallback_eligibility_checks(row)
    paths: dict[str, dict[str, bool]] = {}
    for candidate in cast(list[object], evaluated):
        if not isinstance(candidate, dict):
            continue
        path_item = cast(dict[str, object], candidate)
        path_name = path_item.get("path_code")
        checks = path_item.get("required_checks")
        if not isinstance(path_name, str) or not isinstance(checks, list):
            continue
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,56}", path_name):
            continue
        named: dict[str, bool] = {}
        for entry in cast(list[object], checks):
            if not isinstance(entry, dict):
                continue
            check = cast(dict[str, object], entry)
            check_name = check.get("check_code")
            if (isinstance(check_name, str) and
                    re.fullmatch(r"[a-z][a-z0-9_]{0,56}", check_name) and
                    type(check.get("passed")) is bool and check.get("required") is True):
                named[check_name.upper() + "_V1"] = check["passed"] is True
        if named:
            paths[path_name.upper().replace("-", "_") + "_V1"] = named
    return paths or _fallback_eligibility_checks(row)


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
                + "failure_code,latency_ms,attempt) VALUES(?,?,?,?,?,?,?)",
                (hostname, phase, status_code, error_code,
                 http_failure_code(phase, status_code, error_code),
                 latency_ms, attempt),
            )

    def record_page(self, hostname: str, source: str,
                    page_type: str, outcome: str, *,
                    reason_codes: tuple[str, ...] = (),
                    positive_signals: tuple[str, ...] = (),
                    negative_signals: tuple[str, ...] = ()) -> None:
        with self.lock, self.connection:
            self.connection.execute(
                "INSERT INTO page_events(hostname,source,page_type,outcome,"
                + "reason_codes_json,positive_signals_json,negative_signals_json) "
                + "VALUES(?,?,?,?,?,?,?)",
                (hostname, source, page_type, outcome,
                 _safe_codes((page_outcome_code(outcome),) + reason_codes),
                 _safe_codes(positive_signals), _safe_codes(negative_signals)),
            )

    def record_metrics(self, scope: str, metrics: dict[str, object]) -> None:
        """Checkpoint flat integer metrics separately by run or lifetime scope."""
        if scope not in {"current_run", "lifetime_state", "candidate_snapshot"}:
            raise ValueError("invalid report metric scope")
        with self.lock, self.connection:
            for name, value in metrics.items():
                if type(value) is int and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name):
                    self.connection.execute(
                        "INSERT INTO run_metrics(scope,metric,value) VALUES(?,?,?) "
                        + "ON CONFLICT(scope,metric) DO UPDATE SET value=excluded.value",
                        (scope, name, value),
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
        db.execute("PRAGMA foreign_keys=ON")
        current_id = cast(
            tuple[str] | None, db.execute(
                "SELECT value_json FROM metadata WHERE key='run_id'"
            ).fetchone(),
        )
        if current_id is None or json.loads(current_id[0]) != run_id:
            raise ValueError("run report ID mismatch")
        db.execute("DELETE FROM eligibility_checks")
        db.execute("DELETE FROM eligibility_paths")
        db.execute("DELETE FROM candidates")
        db.execute("DELETE FROM missing_evidence")
        db.execute("DELETE FROM source_attribution")
        db.execute("DELETE FROM source_yield")
        db.execute("DELETE FROM run_metrics")
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
                "eligibility_path",
            )
            normalized = {key: row[key] for key in allow if key in row}
            decision = str(detail.get("code", "unknown"))
            path_value = row.get("eligibility_path")
            qualified_path = (path_value if isinstance(path_value, str)
                              and len(path_value) <= 80 else "")
            db.execute(
                "INSERT INTO candidates(hostname,status,review_eligible,"
                + "decision_code,reason_code,qualification_path,data_json) "
                + "VALUES(?,?,?,?,?,?,?)",
                (host, str(row.get("status", "")),
                 int(detail.get("review_eligible") is True), decision,
                 _CANDIDATE_CODES.get(decision, "CANDIDATE_UNKNOWN_V1"),
                 qualified_path,
                 json.dumps(normalized, ensure_ascii=False, sort_keys=True)),
            )
            for path_code, checks in _eligibility_checks(row).items():
                db.execute(
                    "INSERT INTO eligibility_paths(hostname,path_code,qualified) "
                    + "VALUES(?,?,?)", (host, path_code, int(all(checks.values()))),
                )
                db.executemany(
                    "INSERT INTO eligibility_checks(hostname,path_code,check_code,"
                    + "passed,required) VALUES(?,?,?,?,?)",
                    ((host, path_code, check_code, int(passed), 1)
                     for check_code, passed in checks.items()),
                )
            gaps = detail.get("missing_evidence", [])
            if isinstance(gaps, list):
                for gap in cast(list[object], gaps):
                    if isinstance(gap, str):
                        db.execute(
                            "INSERT OR IGNORE INTO missing_evidence"
                            + "(hostname,missing_code,reason_code) VALUES(?,?,?)",
                            (host, gap, _missing_reason_code(gap)),
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
        # Distinguish missing homepage visits from negative inspection. This
        # diagnoses incomplete promising sites without storing fetched URLs or
        # untrusted page text in the report.
        homepage_outcomes: dict[str, set[str]] = {}
        for host, outcome in cast(list[tuple[str, str]], db.execute(
            "SELECT hostname,outcome FROM page_events WHERE page_type='homepage'"
        ).fetchall()):
            homepage_outcomes.setdefault(host, set()).add(outcome)
        homepage_checks: dict[str, int] = {
            "not_inspected_this_run": 0,
            "inspected_not_topical": 0,
            "inspected_but_unreachable": 0,
        }
        for row in rows:
            if (row.get("status") != "review"
                    or not isinstance(row.get("hostname"), str)
                    or type(row.get("independent_families")) is not int
                    or cast(int, row["independent_families"]) < 1
                    or type(row.get("onsite_content_pages")) is not int
                    or cast(int, row["onsite_content_pages"]) < 2):
                continue
            evidence = row.get("evidence")
            if (isinstance(evidence, list) and
                    any(isinstance(item, str) and item.startswith("site-homepage:")
                        for item in cast(list[object], evidence))):
                continue
            outcomes = homepage_outcomes.get(cast(str, row["hostname"]), set())
            key = ("not_inspected_this_run" if not outcomes
                   else "inspected_not_topical" if "homepage_unverified" in outcomes
                   else "inspected_but_unreachable")
            homepage_checks[key] += 1
        topical = {
            host for host, outcomes in homepage_outcomes.items()
            if "homepage_topical" in outcomes
        }
        internal = {
            host for (host,) in cast(list[tuple[str]], db.execute(
                "SELECT DISTINCT hostname FROM page_events WHERE page_type='content'"
            ).fetchall())
        }
        diagnostics["promising_missing_homepage"] = homepage_checks
        diagnostics["topical_homepages_without_content_inspection"] = len(topical - internal)
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
            # Verified *during this invocation*, not merely represented by
            # stale persistent evidence. Cross-check current page events with
            # the final review-eligible ledger for a defensible source funnel.
            verified_now = cast(int, db.execute(
                "SELECT COUNT(DISTINCT p.hostname) FROM page_events p "
                + "JOIN source_attribution a ON a.hostname=p.hostname "
                + "JOIN candidates c ON c.hostname=p.hostname "
                + "WHERE a.source=? AND p.outcome='content_verified' "
                + "AND c.review_eligible=1",
                (source,),
            ).fetchone()[0])
            db.execute(
                "INSERT INTO source_yield(source,attempted_hosts,"
                + "candidate_hosts_in_ledger,review_eligible_hosts,newly_verified_hosts)"
                + " VALUES(?,?,?,?,?)",
                (source, attempted if type(attempted) is int else 0,
                 in_ledger if type(in_ledger) is int else 0,
                 eligible if type(eligible) is int else 0,
                 verified_now),
            )
        # Query-friendly counters with explicit scope: invocation, cumulative
        # durable frontier, and full persistent candidate snapshot.
        current = report.get("current_run")
        current_data = cast(dict[str, object], current) if isinstance(current, dict) else {}
        funnel_raw = current_data.get("funnel")
        funnel = cast(dict[str, object], funnel_raw) if isinstance(funnel_raw, dict) else {}
        lifetime_raw = report.get("lifetime_state")
        lifetime = (cast(dict[str, object], lifetime_raw)
                    if isinstance(lifetime_raw, dict) else {})
        for scope, metrics in (
            ("current_run", {**funnel, "requests": current_data.get("requests"),
                             "request_budget": current_data.get("request_budget")}),
            ("lifetime_state", lifetime),
            ("candidate_snapshot", {"candidates": len(rows),
                                    "review_eligible": sum(
                                        d.get("review_eligible") is True for d in decisions
                                    )}),
        ):
            for metric, value in metrics.items():
                if type(value) is int and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", metric):
                    db.execute(
                        "INSERT INTO run_metrics(scope,metric,value) VALUES(?,?,?)",
                        (scope, metric, value),
                    )
        failure_counts = cast(list[tuple[str, int]], db.execute(
            "SELECT failure_code,COUNT(*) FROM http_requests "
            + "WHERE failure_code IS NOT NULL GROUP BY failure_code"
        ).fetchall())
        for code, count in failure_counts:
            db.execute(
                "INSERT OR REPLACE INTO run_metrics(scope,metric,value) VALUES(?,?,?)",
                ("current_run", "fail_" + code.lower(), count),
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
