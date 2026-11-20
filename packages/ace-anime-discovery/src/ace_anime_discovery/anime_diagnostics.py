"""Pure, privacy-conscious diagnostics for anime discovery review.

Metrics derived from the persistent candidate ledger are snapshots, NOT a
measurement of the current crawl. The source funnel uses only hosts actually
inspected in the current crawl. No URLs, query strings or page bodies are kept.
"""
from __future__ import annotations

from collections import Counter
from typing import cast


def source_group(source: str) -> str:
    """Bound untrusted source names into stable, comparable dimensions."""
    family, _, detail = source.partition(":")
    if family == "brave-search":
        # Language codes only, never raw search queries.
        return "brave-search:" + detail if detail.isalpha() and len(detail) <= 8 else "brave-search"
    if family in {"catalog", "joined-candidate", "public-index", "outbound",
                  "internal", "sitemap"}:
        return family
    return "other"


def candidate_decision(row: dict[str, object], *, already_approved: bool = False,
                       already_in_pro: bool = False) -> dict[str, object]:
    """Explain why a candidate is or is not ready for human review."""
    status = row.get("status")
    eligible = row.get("review_eligible") is True
    evidence = row.get("evidence")
    markers: set[str] = (
        {v for v in cast(list[object], evidence) if isinstance(v, str)}
        if isinstance(evidence, list) else set()
    )
    families = row.get("independent_families")
    catalog_families = families if type(families) is int else 0
    joined = row.get("joined") is True
    topical_homepage = any(v.startswith("site-homepage:") for v in markers)
    onsite_pages = row.get("onsite_content_pages")
    onsite_titles = row.get("onsite_distinct_titles")
    pages = onsite_pages if type(onsite_pages) is int else 0
    titles = onsite_titles if type(onsite_titles) is int else 0

    # Paths are OR alternatives. Only the nearest path's gaps are considered
    # immediate missing evidence; retain ALL paths' gaps for diagnostics.
    path_gaps: dict[str, list[str]] = {}
    raw_paths = row.get("eligibility_paths")
    if isinstance(raw_paths, list):
        for item in cast(list[object], raw_paths):
            if not isinstance(item, dict):
                continue
            path_data = cast(dict[str, object], item)
            name = path_data.get("path_code")
            gaps = path_data.get("path_gaps")
            if isinstance(name, str) and isinstance(gaps, list):
                path_gaps[name] = [
                    code for code in cast(list[object], gaps) if isinstance(code, str)
                ]
    missing: list[str] = []
    best_path: str | None = None
    if status == "excluded":
        reason = "excluded_by_maintainer_policy"
    elif already_in_pro or status == "pro":
        reason = "already_in_pro"
    elif already_approved:
        reason = "already_approved"
    elif eligible:
        reason = "ready_for_review"
    else:
        reason = "insufficient_verification"
        if path_gaps:
            best_path = min(path_gaps, key=lambda name: (
                len(path_gaps[name]), list(path_gaps).index(name)
            ))
            missing = list(path_gaps[best_path])
        else:
            # Compatibility for older ledgers without explicit path evaluation.
            if catalog_families < 2:
                missing.append("second_independent_catalog")
            if catalog_families == 1 and not joined:
                missing.append("joined_list_match")
            if not topical_homepage:
                missing.append("topical_homepage")
            if catalog_families == 0:
                if pages < 2:
                    missing.append("two_verified_content_pages")
                if titles < 2:
                    missing.append("two_distinct_content_titles")
                if not any(v.startswith("site-internal-page:") for v in markers):
                    missing.append("fetched_content_page_evidence")
    return {
        "code": reason,
        "missing_evidence": missing,
        "alternative_path_gaps": path_gaps,
        "closest_path": best_path,
        "eligibility_path": row.get("eligibility_path"),
        "homepage_topic_observed": topical_homepage,
        "verified_content_pages": pages,
        "distinct_content_titles": titles,
        "independent_catalog_families": catalog_families,
    }


def summarize_candidates(rows: list[dict[str, object]], *,
                         run_host_sources: dict[str, list[str]] | None = None,
                         already_in_pro: set[str] | None = None,
                         already_approved: set[str] | None = None,
                         ) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Derive a candidate snapshot plus source yield from inspected run hosts."""
    pro = already_in_pro or set()
    approved = already_approved or set()
    reasons: Counter[str] = Counter()
    gaps: Counter[str] = Counter()
    decisions: list[dict[str, object]] = []
    by_host: dict[str, dict[str, object]] = {}
    for row in rows:
        host = row.get("hostname")
        if not isinstance(host, str):
            continue
        detail = candidate_decision(row, already_approved=host in approved,
                                    already_in_pro=host in pro)
        reasons[cast(str, detail["code"])] += 1
        for gap in cast(list[str], detail["missing_evidence"]):
            gaps[gap] += 1
        record = {
            "hostname": host, "status": row.get("status"),
            "review_eligible": row.get("review_eligible") is True
                               and host not in pro | approved,
            **detail,
        }
        decisions.append(record)
        by_host[host] = record

    sources: dict[str, dict[str, int]] = {}
    # A host may come from multiple source groups: these are attribution
    # counts, NOT mutually exclusive fractions of the total.
    for host, groups in (run_host_sources or {}).items():
        for name in set(groups):
            bucket = sources.setdefault(name, {
                "attempted_hosts": 0,
                "candidate_hosts_in_ledger": 0,
                "review_eligible_hosts": 0,
            })
            bucket["attempted_hosts"] += 1
            match = by_host.get(host)
            if match is not None:
                bucket["candidate_hosts_in_ledger"] += 1
                if match["review_eligible"] is True:
                    bucket["review_eligible_hosts"] += 1

    result: dict[str, object] = {
        "snapshot_candidates": len(decisions),
        "decision_codes": dict(sorted(reasons.items())),
        "missing_evidence": dict(sorted(gaps.items())),
        "source_yield_current_run": dict(sorted(sources.items())),
        "source_attribution_note": "Host attribution can overlap across sources;"
                                   + " candidate decisions use the current persistent ledger snapshot.",
    }
    return result, decisions
