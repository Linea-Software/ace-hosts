"""Turn discovery findings into a maintainer-reviewable anime/manga seed PR.

Only a merged seed entry can affect the published anime/manga list. Require
independent catalog corroboration before adding a domain to a proposal; homepage
keywords alone are insufficient and are vulnerable to SEO spam.
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
    *,
    max_new: int = 100,
) -> list[str]:
    """Append safe proposals to the reviewed seed; write a human-readable PR body."""
    if not 1 <= max_new <= 250:
        raise ValueError("max_new must be between 1 and 250")
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
                        if (parsed.scheme == "https" and parsed.hostname == host
                                and content_page_path(parsed.path)
                                and normalize_hostname(raw) == host):
                            content_urls.add(parsed.path)
                    except ValueError:
                        continue
            web_original = (
                families == 0 and scraped
                and row.get("verification_method") == "multi-page-website"
                and row.get("review_eligible") is True
                and len(content_urls) >= 2
                and type(row.get("onsite_distinct_titles")) is int
                and cast(int, row["onsite_distinct_titles"]) >= 2
                and any(key.startswith("site-internal-page:") for key in scrapes)
            )
            if families >= 2 or (families >= 1 and joined and scraped) or web_original:
                eligible.append(row)

    # Keep web-original discoveries visible even when catalog candidates
    # are numerous. Each source gets fair slots; unused slots are filled.
    web_originals = sorted(
        (row for row in eligible if row.get("verification_method") == "multi-page-website"),
        key=lambda row: (-cast(int, row.get("onsite_content_pages", 0)),
                         cast(str, row["hostname"])),
    )
    catalogs = sorted(
        (row for row in eligible if row.get("verification_method") != "multi-page-website"),
        key=lambda row: (-cast(int, row["independent_families"]),
                         cast(str, row["hostname"])),
    )
    proposals: list[dict[str, object]] = []
    while len(proposals) < max_new and (web_originals or catalogs):
        for group in (web_originals, catalogs):
            if group and len(proposals) < max_new:
                proposals.append(group.pop(0))
    names = sorted(cast(str, row["hostname"]) for row in proposals)
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
        f"**{len(names)} new {'domain' if len(names) == 1 else 'domains'} proposed**. When you review and merge this PR into `main`,",
        "the next release will automatically add them to **Anime/Manga Pro**.",
        "Published lists remain unchanged until this PR is merged.",
        "",
        "Discovery combines independent catalogs, multilingual public-web search,",
        "robots-compliant recursive links, sitemaps and observed site metadata.",
        "Static HTTP and bounded Chromium rendering are used where permitted.",
        "Web-original sites absent from existing catalogs can be proposed after",
        "fetching a thematic homepage and at least two distinct internal content pages.",
        "Other proposals need two independent catalog families, or one",
        "catalog family plus homepage evidence and a joined-list match.",
        "Proposed domains are **never published** without a merged PR.",
        "",
        "| Domain | Sources | Categories | Languages | Independent catalogs | Content pages | HTML | JS browser | Sitemap | In joined list | Verification level | Confidence | Verification method | Discovery source | Evidence |",
        "| --- | --- | --- | --- | ---: | ---: | :---: | :---: | :---: | :---: | --- | ---: | --- | --- | --- |",
    ]
    for row in sorted(proposals, key=lambda item: cast(str, item["hostname"])):
        evidence = cast(list[str], row["evidence"])
        sources = row.get("repositories", [])
        source_list = ", ".join(
            source for source in cast(list[str], sources)
            if source.isascii() and source.replace("-", "").replace("_", "").isalnum()
        ) if isinstance(sources, list) else ""
        categories = row.get("categories", [])
        languages = row.get("languages", [])
        kinds = ", ".join(str(v) for v in cast(list[object], categories)) if isinstance(categories, list) else ""
        langs = ", ".join(str(v) for v in cast(list[object], languages)) if isinstance(languages, list) else ""
        discovery_sources = row.get("discovery_sources", [])
        origins = ", ".join(str(url).replace("|", "%7C")[:120] for url in
                            cast(list[object], discovery_sources)[:2]) if isinstance(discovery_sources, list) else ""
        method = str(row.get("verification_method", "catalog-and-site" if
                            any(e.startswith("site-homepage:") for e in evidence) else "catalogs"))
        level = ("Web-original (multi-page)" if method == "multi-page-website"
                 else "Confirmed" if cast(int, row["independent_families"]) >= 2
                 else "Catalog + homepage")
        details = ", ".join(str(value).replace("|", "/")[:55] for value in evidence[:5])
        body.append("".join([
            f"| `{row['hostname']}` | {source_list} | {kinds} | {langs} | ",
            f"{row['independent_families']} | {row.get('onsite_content_pages', 0)} | ",
            f"{'yes' if 'site-homepage:html-title-description' in evidence else 'no'} | ",
            f"{'yes' if 'site-homepage:html-rendered' in evidence else 'no'} | ",
            f"{'yes' if 'site-sitemap:sitemap-paths' in evidence else 'no'} | ",
            f"{'yes' if row['joined'] is True else 'no'} | ",
            f"{level} | {row.get('confidence', 0)} | {method} | {origins} | {details} |",
        ]))
    body.extend([
        "",
        "**Before merging:** verify that each domain is a relevant thematic site, not a",
        "general-purpose service, an unwanted mirror, or a false positive. Remove lines",
        "from `seeds/anime-manga.txt` directly in the PR.",
        "The whitelist and category exclusions still take precedence.",
        "",
        "Raw discovery evidence and candidates not proposed are in the artifacts",
        "from the Anime Discovery workflow.",
    ])
    utils.atomic_write_lines(body_path, body)
    return names


class ProposalArgs(argparse.Namespace):
    ledger: Path = utils.PROJECT_ROOT / "downloads" / "anime-discovery" / "candidates.jsonl"
    approved_file: Path = utils.PROJECT_ROOT / "seeds" / "anime-manga.txt"
    pro_file: Path = utils.PROJECT_ROOT / "hosts" / "anime-manga.txt"
    whitelist: Path = utils.PROJECT_ROOT / "whitelist.txt"
    exceptions: Path = utils.PROJECT_ROOT / "exceptions" / "anime-manga.txt"
    body: Path = utils.PROJECT_ROOT / "downloads" / "anime-discovery" / "pr-body.md"
    max_new: int = 100


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=ProposalArgs.ledger)
    parser.add_argument("--approved-file", type=Path, default=ProposalArgs.approved_file)
    parser.add_argument("--pro-file", type=Path, default=ProposalArgs.pro_file)
    parser.add_argument("--whitelist", type=Path, default=ProposalArgs.whitelist)
    parser.add_argument("--exceptions", type=Path, default=ProposalArgs.exceptions)
    parser.add_argument("--body", type=Path, default=ProposalArgs.body)
    parser.add_argument("--max-new", type=int, default=100)
    args = parser.parse_args(argv, namespace=ProposalArgs())
    try:
        sites = propose_sites(args.ledger, args.approved_file, args.pro_file, args.whitelist,
                              args.exceptions, args.body, max_new=args.max_new)
    except (ValueError, OSError, json.JSONDecodeError) as error:
        parser.exit(1, f"anime discovery proposal failed: {error}\n")
    print(f"Anime Discovery: {len(sites)} new domain(s) proposed for PR")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
