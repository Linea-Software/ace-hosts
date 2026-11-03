"""Turn discovery findings into a maintainer-reviewable Extended seed PR.

Only a merged seed entry can affect the release. Catalog corroboration or
bounded homepage scraping makes a domain eligible for *proposal*, never for
unreviewed publication.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import cast

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import utils
from scripts.anime_discovery import safe_hostname


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
            if row.get("status") not in {"validated-extended", "website-verified", "review"}:
                continue
            families = row.get("independent_families")
            if not isinstance(families, int) or isinstance(families, bool):
                continue
            evidence = row.get("evidence")
            scrapes: set[str] = set(cast(list[str], evidence)) if isinstance(evidence, list) else set()
            scraped = bool(
                {"site-homepage:html-title-description", "site-homepage:html-rendered", "site-sitemap:sitemap-paths"} & scrapes
            )
            joined = row.get("joined") is True
            web_sources = row.get("repositories", [])
            web_lead = (isinstance(web_sources, list) and
                        bool({"web-link", "web-directory", "brave-search", "site-outbound"}
                             & set(cast(list[str], web_sources))))
            website_verified = (row.get("verification_method") == "website-metadata"
                                and row.get("review_eligible") is True
                                and web_lead and scraped)
            if families >= 2 or (families >= 1 and joined and scraped) or website_verified:
                eligible.append(row)

    # Prefer confirmed independent catalogs, then website-verified candidates.
    eligible.sort(key=lambda row: (
        -int(cast(int, row["independent_families"]) >= 2),
        -int(row.get("verification_method") == "website-metadata"),
        -int(bool({"site-homepage:html-title-description", "site-homepage:html-rendered", "site-sitemap:sitemap-paths"}
                  & set(cast(list[str], row["evidence"])))),
        cast(str, row["hostname"]),
    ))
    proposals = eligible[:max_new]
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
        "the next release will automatically add them to **Anime/Manga Extended**.",
        "Published lists remain unchanged until this PR is merged.",
        "",
        "Discovery combines independent catalogs, multilingual public-web search,",
        "robots-compliant recursive links, sitemaps and verified site metadata.",
        "Static HTTP and bounded Chromium rendering are used where permitted.",
        "Scraped results and external links are **never published** "
        + "without human review and a merged PR.",
        "",
        "| Domain | Sources | Categories | Languages | Independent catalogs | HTML | JS browser | Sitemap | In joined list | Verification level | Confidence | Verification method | Discovery source | Evidence |",
        "| --- | --- | --- | --- | ---: | :---: | :---: | :---: | :---: | --- | ---: | --- | --- | --- |",
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
        level = "Website-verified" if method == "website-metadata" else "Confirmed" if cast(int, row["independent_families"]) >= 2 else "Catalog + website"
        details = ", ".join(str(value).replace("|", "/")[:55] for value in evidence[:5])
        body.append("".join([
            f"| `{row['hostname']}` | {source_list} | {kinds} | {langs} | ",
            f"{row['independent_families']} | ",
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
        "from `seeds/anime-manga-extended.txt` directly in the PR.",
        "The whitelist and category exclusions still take precedence.",
        "",
        "Raw discovery evidence and candidates not proposed are in the artifacts",
        "from the Anime Discovery workflow.",
    ])
    utils.atomic_write_lines(body_path, body)
    return names


class ProposalArgs(argparse.Namespace):
    ledger: Path = utils.PROJECT_ROOT / "downloads" / "anime-discovery" / "candidates.jsonl"
    approved_file: Path = utils.PROJECT_ROOT / "seeds" / "anime-manga-extended.txt"
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
