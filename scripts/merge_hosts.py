"""Merge downloaded host lists into a single clean, deduplicated file.

This combines the two maintenance scripts of the archived columndeeply/hosts
repository:

- ``cleanup.sh`` — remove empty lines, comments, multiple whitespaces, tabs
  and trailing whitespace; normalize every line to ``127.0.0.1 <domain>``;
  remove whitelisted domains; remove duplicates; sort.
- ``merger.sh`` — merge clean lists with the main list, deduplicate, sort and
  split the result into 90 MB chunks.

Run this after ``download_sources.py`` (or point ``--input`` at any hosts /
adblock / plain-domain lists). Use ``--split`` to also split the merged file
into GitHub-friendly chunks.
"""

from __future__ import annotations

import argparse
import glob
import itertools
import logging
import sys
from pathlib import Path

from tqdm import tqdm

if __package__ in (None, ""):
    # Running directly as a script (python scripts/merge_hosts.py): make the
    # project root importable so the package import below works in every
    # execution mode (script, module, installed console script).
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import split_hosts, utils

logger = logging.getLogger("ace-hosts.merge")


def resolve_input_files(patterns: list[str], config: dict[str, str]) -> list[Path]:
    """Determine the files to merge.

    Priority: ``--input`` flags > ``INPUT_FILES`` env var > everything in the
    download directory. Glob patterns are expanded (needed on Windows, where
    the shell does not do it).
    """
    candidates = list(patterns)
    if not candidates and config.get("INPUT_FILES", "").strip():
        candidates = [entry.strip() for entry in config["INPUT_FILES"].split(",") if entry.strip()]
    if not candidates:
        candidates = [str(utils.PROJECT_ROOT / config["DOWNLOAD_DIR"] / "*")]

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
    """Merge *files* into a list of unique, whitelist-filtered domains."""
    stats: dict[str, int] = {"files": len(files), "raw_domains": 0, "whitelisted": 0, "unique": 0}
    domains: set[str] = set()
    ordered: list[str] = []

    for path in tqdm(files, desc="merging sources", unit="file", disable=not show_progress):
        count = 0
        for domain in utils.iter_domains(path):
            count += 1
            domains.add(domain)
            if not dedupe:
                ordered.append(domain)
        stats["raw_domains"] += count
        logger.info("  %s: %d domain(s)", path.name, count)

    result = list(domains) if dedupe else ordered

    if whitelist_domains:
        before = len(result)
        result = [domain for domain in result if domain not in whitelist_domains]
        stats["whitelisted"] = before - len(result)

    stats["unique"] = len(set(result))
    return result, stats


class MergeArgs(argparse.Namespace):
    """Typed arguments for the merge CLI (see main())."""

    input: list[str] | None = None
    output: str | None = None
    whitelist: str | None = None
    no_dedupe: bool = False
    no_sort: bool = False
    normalize_ip: str | None = None
    no_progress: bool = False
    split: bool = False
    split_mb: float | None = None
    split_prefix: str | None = None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ace-hosts-merge",
        description="Merge host lists into one clean, deduplicated, sorted file.",
    )
    parser.add_argument("--input", action="append", default=[], metavar="PATH", help="input file or glob (repeatable)")
    parser.add_argument("--output", metavar="PATH", help="merged output file (default: $OUTPUT_DIR/$MERGED_FILENAME)")
    parser.add_argument("--whitelist", metavar="PATH", help="file with domains to exclude (default: $WHITELIST_FILE)")
    parser.add_argument("--no-dedupe", action="store_true", help="keep duplicate domains")
    parser.add_argument("--no-sort", action="store_true", help="do not sort the merged list")
    parser.add_argument("--normalize-ip", metavar="IP", help="IP prefix for every line (default: $NORMALIZE_IP)")
    parser.add_argument("--no-progress", action="store_true", help="disable progress bars")
    parser.add_argument("--split", action="store_true", help="split the merged file into chunks afterwards")
    parser.add_argument("--split-mb", type=float, metavar="MIB", help="max chunk size in MiB (default: $SPLIT_CHUNK_MB)")
    parser.add_argument("--split-prefix", metavar="NAME", help="chunk filename prefix (default: $SPLIT_PREFIX)")
    args: MergeArgs = parser.parse_args(argv, namespace=MergeArgs())

    config = utils.load_config()
    utils.setup_logging(config["LOG_LEVEL"])

    files = resolve_input_files(args.input or [], config)
    if not files:
        logger.error(
            "no input files found — run `ace-hosts-download` first or pass --input <file|glob>"
        )
        return 1

    whitelist_path = Path(args.whitelist) if args.whitelist else Path(config["WHITELIST_FILE"])
    if not whitelist_path.is_absolute():
        whitelist_path = utils.PROJECT_ROOT / whitelist_path
    whitelist_domains = load_whitelist(whitelist_path)

    dedupe = not args.no_dedupe and utils.config_bool(config["REMOVE_DUPLICATES"])
    domains, stats = merge_files(files, whitelist_domains, dedupe=dedupe, show_progress=not args.no_progress)

    if not args.no_sort and utils.config_bool(config["SORT_OUTPUT"]):
        domains.sort()

    out_path = Path(args.output) if args.output else Path(config["OUTPUT_DIR"]) / config["MERGED_FILENAME"]
    if not out_path.is_absolute():
        out_path = utils.PROJECT_ROOT / out_path

    normalize_ip = args.normalize_ip or config["NORMALIZE_IP"]
    header = utils.hosts_header(len(domains), [path.name for path in files])
    # Stream the body through a generator so we never hold two copies of the
    # list in memory (important for 10M+ domain lists).
    body = (f"{normalize_ip} {domain}" for domain in domains)
    utils.atomic_write_lines(out_path, itertools.chain(header, body))

    logger.info("merged %d unique domain(s) into %s", len(domains), out_path)
    logger.info("stats: %s", stats)

    if args.split:
        max_mb = args.split_mb if args.split_mb is not None else float(config["SPLIT_CHUNK_MB"])
        prefix = args.split_prefix or config["SPLIT_PREFIX"]
        chunks = split_hosts.split_file(out_path, out_path.parent, max_mb=max_mb, prefix=prefix)
        logger.info("created %d chunk(s) in %s", len(chunks), out_path.parent)
    return 0


if __name__ == "__main__":
    sys.exit(main())
