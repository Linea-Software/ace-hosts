"""Turn discovery findings into a maintainer-reviewable anime/manga seed PR.

Only a merged seed entry can affect the published anime/manga list. Propose
every qualified domain without a numeric cap. Independent catalogs or multi-page
website verification are required; homepage keywords alone are insufficient.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import cast

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ace_anime_discovery import utils
from ace_anime_discovery.anime_discovery import content_page_path, safe_hostname, normalize_hostname
from urllib.parse import urlsplit


def propose_sites(
    ledger_path: Path,
    approved_file: Path,
    pro_file: Path,
    whitelist_file: Path,
    exceptions_file: Path,
    body_path: Path,
) -> list[str]:
    """Append all qualified proposals and write a concise review PR body."""
    for path in (ledger_path, approved_file, pro_file):
        if not path.is_file():
            raise ValueError(f"missing current discovery input: {path}")
    existing = set(utils.iter_domains(approved_file))
    pro = set(utils.iter_domains(pro_file))
    excluded: set[str] = set(utils.iter_domains(whitelist_file)) if whitelist_file.is_file() else set()
    if exceptions_file.is_file():
        excluded.update(utils.iter_domains(exceptions_file))

    eligible: list[dict[str, object]] = []
    with ledger_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            item = cast(object, json.loads(line))
            if not isinstance(item, dict):
                raise ValueError("invalid discovery ledger")
            row = cast(dict[str, object], item)
            host = row.get("hostname")
            if not isinstance(host, str) or not safe_hostname(host):
                continue
            if host in pro or host in existing or host in excluded:
                continue
            if row.get("status") not in {"validated-candidate", "website-verified", "review"}:
                continue
            families = row.get("independent_families")
            if not isinstance(families, int) or isinstance(families, bool):
                continue
            evidence = row.get("evidence")
            scrapes: set[str] = set(cast(list[str], evidence)) if isinstance(evidence, list) else set()
            scraped = bool(
                {"site-homepage:html-title-description", "site-homepage:html-rendered"} & scrapes
            )
            joined = row.get("joined") is True
            # The site itself can be the discovery source: a real homepage
            # plus two fetched, distinct same-host content pages is eligible
            # for human review without appearing in any existing catalog.
            urls = row.get("onsite_content_urls")
            content_urls: set[str] = set()
            if isinstance(urls, list):
                for raw in cast(list[object], urls):
                    if not isinstance(raw, str):
                        continue
                    try:
                        parsed = urlsplit(raw)
                        # Explicit structurally verified pages may use
                        # framework-specific or language-prefixed opaque paths.
                        verified_route = "site-internal-page:verified-content-page" in scrapes
                        if (parsed.scheme == "https" and parsed.hostname == host
                                and parsed.path not in {"", "/"}
                                and (content_page_path(parsed.path) or verified_route)
                                and normalize_hostname(raw) == host):
                            content_urls.add(parsed.path)
                    except ValueError:
                        continue
            # Revalidate direct evidence here: candidates.jsonl is untrusted
            # input and an eligibility_path label is never authorization alone.
            multi_page_verified = (
                row.get("review_eligible") is True
                and len(content_urls) >= 2
                and type(row.get("onsite_distinct_titles")) is int
                and cast(int, row["onsite_distinct_titles"]) >= 2
                and any(key.startswith("site-internal-page:") for key in scrapes)
            )
            web_original = (
                families == 0 and scraped and multi_page_verified
                and row.get("verification_method") == "multi-page-website"
            )
            catalog_verified = (
                families >= 1 and multi_page_verified
                and row.get("verification_method") == "catalog-and-verified-pages"
                and (
                    row.get("eligibility_path") == "catalog-verified-content"
                    and scraped
                    or row.get("eligibility_path") == "catalog-verified-pages"
                    and "site-internal-page:verified-content-page" in scrapes
                )
            )
            if (families >= 2 or (families >= 1 and joined and scraped)
                    or catalog_verified or web_original):
                eligible.append(row)

    # Do not truncate: every qualified domain goes into the review PR.
    # The complete list lives in the seed diff, not in the PR description.
    names = sorted(cast(str, row["hostname"]) for row in eligible)
    web_original_count = sum(
        row.get("verification_method") == "multi-page-website" for row in eligible
    )
    catalog_count = len(eligible) - web_original_count
    if names:
        original = approved_file.read_text(encoding="utf-8")
        suffix = "" if original.endswith("\n") or not original else "\n"
        utils.atomic_write_lines(
            approved_file,
            (original + suffix + "\n".join(names)).splitlines(),
        )

    body = [
        "## New anime and manga sites for review",
        "",
        f"**{len(names)} new {'domain' if len(names) == 1 else 'domains'} proposed** for Anime/Manga Pro.",
        "",
        f"- Web-original sites verified through multiple pages: **{web_original_count}**",
        f"- Catalog-supported sites: **{catalog_count}**",
        "",
        "Review the complete list in the **Files changed** tab (`seeds/anime-manga.txt`).",
        "Remove unrelated sites, mirrors, or false positives from the seed file before merging.",
        "The whitelist and category exclusions still apply.",
        "",
        "Full per-domain evidence, discovery sources and crawl reports are available in the workflow artifacts.",
        "**Nothing is published until this PR is reviewed and merged.**",
    ]
    utils.atomic_write_lines(body_path, body)
    return names


class ProposalArgs(argparse.Namespace):
    ledger: Path = utils.PROJECT_ROOT / "downloads" / "anime-discovery" / "candidates.jsonl"
    approved_file: Path = utils.PROJECT_ROOT / "seeds" / "anime-manga.txt"
    pro_file: Path = utils.PROJECT_ROOT / "hosts" / "anime-manga.txt"
    whitelist: Path = utils.PROJECT_ROOT / "whitelist.txt"
    exceptions: Path = utils.PROJECT_ROOT / "exceptions" / "anime-manga.txt"
    body: Path = utils.PROJECT_ROOT / "downloads" / "anime-discovery" / "pr-body.md"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=ProposalArgs.ledger)
    parser.add_argument("--approved-file", type=Path, default=ProposalArgs.approved_file)
    parser.add_argument("--pro-file", type=Path, default=ProposalArgs.pro_file)
    parser.add_argument("--whitelist", type=Path, default=ProposalArgs.whitelist)
    parser.add_argument("--exceptions", type=Path, default=ProposalArgs.exceptions)
    parser.add_argument("--body", type=Path, default=ProposalArgs.body)
    args = parser.parse_args(argv, namespace=ProposalArgs())
    try:
        sites = propose_sites(args.ledger, args.approved_file, args.pro_file, args.whitelist,
                              args.exceptions, args.body)
    except (ValueError, OSError, json.JSONDecodeError) as error:
        parser.exit(1, f"anime discovery proposal failed: {error}\n")
    print(f"Anime Discovery: {len(sites)} new domain(s) proposed for PR")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
