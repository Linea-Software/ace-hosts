"""Merge downloaded host lists into clean category-specific blocklists."""

from __future__ import annotations

import argparse
import glob
import itertools
import logging
import sys
from pathlib import Path

from tqdm import tqdm

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import source_catalog, split_hosts, utils

logger = logging.getLogger("ace-hosts.merge")


def _configured_dir(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else utils.PROJECT_ROOT / path


def _manifest_inputs(category_dir: Path) -> list[str] | None:
    manifest = category_dir / source_catalog.DOWNLOAD_MANIFEST
    if not manifest.is_file():
        return None
    names = [line.strip() for line in manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [str(category_dir / name) for name in names]


def resolve_input_files(
    patterns: list[str],
    config: dict[str, str],
    category: str = source_catalog.DEFAULT_CATEGORY,
) -> list[Path]:
    """Determine the files to merge for one category.

    Priority: explicit ``--input`` > ``INPUT_FILES`` > the downloader manifest
    for ``downloads/<category>`` > files in that category directory. For the
    adult category only, a flat legacy ``downloads/*`` layout is accepted when
    ``downloads/adult`` does not yet exist.
    """
    candidates = list(patterns)
    if not candidates and config.get("INPUT_FILES", "").strip():
        candidates = [entry.strip() for entry in config["INPUT_FILES"].split(",") if entry.strip()]
    if not candidates:
        download_dir = _configured_dir(config["DOWNLOAD_DIR"])
        category_dir = download_dir / category
        manifest_inputs = _manifest_inputs(category_dir)
        if manifest_inputs is not None:
            candidates = manifest_inputs
        elif category_dir.is_dir():
            candidates = [str(category_dir / "*")]
        elif category == source_catalog.DEFAULT_CATEGORY:
            candidates = [str(download_dir / "*")]
        else:
            candidates = [str(category_dir / "*")]

    files: list[Path] = []
    seen: set[Path] = set()
    for pattern in candidates:
        for match in sorted(glob.glob(pattern)):
            path = Path(match)
            resolved = path.resolve()
            if path.is_file() and resolved not in seen:
                seen.add(resolved)
                files.append(path)
    return files


def load_whitelist(path: Path | None) -> set[str]:
    """Load the optional whitelist (domains that must never be blocked)."""
    if path is None or not path.is_file():
        return set()
    whitelist = set(utils.iter_domains(path))
    logger.info("whitelist: %d domain(s) from %s", len(whitelist), path)
    return whitelist


def merge_files(
    files: list[Path],
    whitelist_domains: set[str],
    dedupe: bool = True,
    show_progress: bool = True,
) -> tuple[list[str], dict[str, int]]:
    """Merge *files* into a whitelist-filtered domain list.

    The default deduplicating path keeps only one set in memory. The explicit
    ``--no-dedupe`` path keeps only the ordered list, avoiding the old behavior
    that unnecessarily retained both representations simultaneously.
    """
    stats: dict[str, int] = {"files": len(files), "raw_domains": 0, "whitelisted": 0, "unique": 0}

    if dedupe:
        unique_domains: set[str] = set()
        for path in tqdm(files, desc="merging sources", unit="file", disable=not show_progress):
            count = 0
            for domain in utils.iter_domains(path):
                count += 1
                unique_domains.add(domain)
            stats["raw_domains"] += count
            logger.info("  %s: %d domain(s)", path.name, count)

        if whitelist_domains:
            before = len(unique_domains)
            unique_domains.difference_update(whitelist_domains)
            stats["whitelisted"] = before - len(unique_domains)
        stats["unique"] = len(unique_domains)
        return list(unique_domains), stats

    ordered: list[str] = []
    for path in tqdm(files, desc="merging sources", unit="file", disable=not show_progress):
        count = 0
        for domain in utils.iter_domains(path):
            count += 1
            ordered.append(domain)
        stats["raw_domains"] += count
        logger.info("  %s: %d domain(s)", path.name, count)
    if whitelist_domains:
        before = len(ordered)
        ordered = [domain for domain in ordered if domain not in whitelist_domains]
        stats["whitelisted"] = before - len(ordered)
    stats["unique"] = len(set(ordered))
    return ordered, stats


def default_output_path(category: str, config: dict[str, str]) -> Path:
    """Return the default merged output while preserving adult asset names."""
    output_dir = _configured_dir(config["OUTPUT_DIR"])
    if category == source_catalog.DEFAULT_CATEGORY:
        return output_dir / config["MERGED_FILENAME"]
    return output_dir / f"{category}.txt"


def default_split_prefix(category: str, config: dict[str, str]) -> str:
    """Return a category-specific chunk prefix with adult compatibility."""
    if category == source_catalog.DEFAULT_CATEGORY:
        return config["SPLIT_PREFIX"]
    return f"{category}-hosts"


class MergeArgs(argparse.Namespace):
    """Typed arguments for the merge CLI (see main())."""

    input: list[str] | None = None
    output: str | None = None
    whitelist: str | None = None
    category: str = source_catalog.DEFAULT_CATEGORY
    all_categories: bool = False
    no_dedupe: bool = False
    no_sort: bool = False
    normalize_ip: str | None = None
    no_progress: bool = False
    split: bool = False
    split_mb: float | None = None
    split_prefix: str | None = None


def _merge_category(
    category: str,
    args: MergeArgs,
    config: dict[str, str],
    whitelist_domains: set[str],
) -> int:
    files = resolve_input_files(args.input or [], config, category)
    if not files:
        logger.error(
            "%s: no input files found — run `ace-hosts-download --category %s` first",
            category,
            category,
        )
        return 1

    dedupe = not args.no_dedupe and utils.config_bool(config["REMOVE_DUPLICATES"])
    domains, stats = merge_files(files, whitelist_domains, dedupe=dedupe, show_progress=not args.no_progress)
    if not args.no_sort and utils.config_bool(config["SORT_OUTPUT"]):
        domains.sort()

    if args.output:
        out_path = Path(args.output)
        if not out_path.is_absolute():
            out_path = utils.PROJECT_ROOT / out_path
    else:
        out_path = default_output_path(category, config)

    normalize_ip = args.normalize_ip or config["NORMALIZE_IP"]
    using_builtin_inputs = not (args.input or []) and not config.get("INPUT_FILES", "").strip()
    source_licenses = source_catalog.licenses_for_category(category) if using_builtin_inputs else ()
    entry_count = len(domains)
    header = utils.hosts_header(
        entry_count,
        [path.name for path in files],
        category=category,
        source_licenses=source_licenses,
    )
    body = (f"{normalize_ip} {domain}" for domain in domains)
    utils.atomic_write_lines(out_path, itertools.chain(header, body))

    logger.info("%s: merged %d unique domain(s) into %s", category, entry_count, out_path)
    logger.info("%s stats: %s", category, stats)
    del domains

    if args.split:
        max_mb = args.split_mb if args.split_mb is not None else float(config["SPLIT_CHUNK_MB"])
        prefix = args.split_prefix or default_split_prefix(category, config)
        chunks = split_hosts.split_file(
            out_path,
            out_path.parent,
            max_mb=max_mb,
            prefix=prefix,
            show_progress=not args.no_progress,
        )
        logger.info("%s: created %d chunk(s) in %s", category, len(chunks), out_path.parent)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ace-hosts-merge",
        description="Merge host lists into clean, category-specific blocklists.",
    )
    parser.add_argument(
        "--category",
        choices=source_catalog.category_names(),
        default=source_catalog.DEFAULT_CATEGORY,
        help=f"category to merge (default: {source_catalog.DEFAULT_CATEGORY})",
    )
    parser.add_argument("--all-categories", action="store_true", help="merge every built-in category")
    parser.add_argument("--input", action="append", default=[], metavar="PATH", help="input file or glob (repeatable)")
    parser.add_argument("--output", metavar="PATH", help="merged output file (single category only)")
    parser.add_argument("--whitelist", metavar="PATH", help="file with domains to exclude (default: $WHITELIST_FILE)")
    parser.add_argument("--no-dedupe", action="store_true", help="keep duplicate domains")
    parser.add_argument("--no-sort", action="store_true", help="do not sort the merged list")
    parser.add_argument("--normalize-ip", metavar="IP", help="IP prefix for every line (default: $NORMALIZE_IP)")
    parser.add_argument("--no-progress", action="store_true", help="disable progress bars")
    parser.add_argument("--split", action="store_true", help="split each merged file into chunks afterwards")
    parser.add_argument("--split-mb", type=float, metavar="MIB", help="max chunk size in MiB (default: $SPLIT_CHUNK_MB)")
    parser.add_argument("--split-prefix", metavar="NAME", help="chunk filename prefix (single category only)")
    args: MergeArgs = parser.parse_args(argv, namespace=MergeArgs())

    config = utils.load_config()
    utils.setup_logging(config["LOG_LEVEL"])
    if args.all_categories and (args.input or args.output or args.split_prefix or config.get("INPUT_FILES", "").strip()):
        parser.error("--all-categories cannot be combined with --input, --output, --split-prefix, or INPUT_FILES")

    whitelist_path = Path(args.whitelist) if args.whitelist else Path(config["WHITELIST_FILE"])
    if not whitelist_path.is_absolute():
        whitelist_path = utils.PROJECT_ROOT / whitelist_path
    whitelist_domains = load_whitelist(whitelist_path)

    categories = source_catalog.category_names() if args.all_categories else (args.category,)
    failed = False
    for category in categories:
        if _merge_category(category, args, config, whitelist_domains) != 0:
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
