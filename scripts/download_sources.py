"""Download category-specific host lists from upstream sources.

Failures are handled gracefully: each source is retried with exponential
backoff, rate limiting is applied between sources, and a source that keeps
failing is skipped instead of aborting the whole category build.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import tempfile
import time
from pathlib import Path

import requests
from tqdm import tqdm

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import source_catalog, utils

logger = logging.getLogger("ace-hosts.download")

# Backwards-compatible public name used by tests/callers: the original pipeline
# had one default source map, which is now the adult category.
DEFAULT_SOURCES: dict[str, str] = source_catalog.sources_for_category(source_catalog.DEFAULT_CATEGORY)
CHUNK_SIZE = 64 * 1024


def parse_sources(raw: str, defaults: dict[str, str] | None = None) -> dict[str, str]:
    """Parse a SOURCES-style string into an ordered ``{name: url}`` map.

    Entries are separated by commas or newlines. Each entry is either a bare
    URL (name derived from the filename) or ``name=url``. User-provided names
    are sanitized so they can never escape the configured download directory.
    """
    if not raw.strip():
        return dict(DEFAULT_SOURCES if defaults is None else defaults)
    sources: dict[str, str] = {}
    for entry in re.split(r"[\n,]+", raw.strip()):
        entry = entry.strip()
        if not entry:
            continue
        if "=" in entry:
            name, _, url = entry.partition("=")
            url = url.strip()
            name = utils.sanitize_source_name(name.strip() or utils.sanitize_source_filename(url))
            sources[name] = url
        else:
            sources[utils.sanitize_source_name(utils.sanitize_source_filename(entry))] = entry
    return sources


def download_path(name: str, url: str, dest_dir: Path) -> Path:
    """Return the safe local path used for a source download."""
    safe_name = utils.sanitize_source_name(name)
    suffix = Path(url.split("?", 1)[0]).suffix or ".txt"
    filename = safe_name if Path(safe_name).suffix else f"{safe_name}{suffix}"
    return dest_dir / filename


def download_source(
    name: str,
    url: str,
    dest_dir: Path,
    timeout: float,
    retries: int,
    backoff: float,
    user_agent: str,
) -> int | None:
    """Download one source atomically into *dest_dir*; return bytes or None.

    A failed refresh never leaves a partially-written source file behind. Any
    previous copy at the same path is removed before the refresh so a later
    merge cannot silently consume stale data after a failed download.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = download_path(name, url, dest_dir)
    dest.unlink(missing_ok=True)

    headers = {"User-Agent": user_agent}
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        fd, tmp_name = tempfile.mkstemp(prefix=dest.name + ".", suffix=".tmp", dir=str(dest_dir))
        try:
            with requests.get(url, stream=True, timeout=timeout, headers=headers) as response:
                response.raise_for_status()
                total = int(response.headers.get("Content-Length") or 0)
                with tqdm(
                    total=total,
                    unit="B",
                    unit_scale=True,
                    desc=f"{name[:28]:<28}",
                    leave=False,
                ) as progress:
                    with os.fdopen(fd, "wb") as handle:
                        fd = -1
                        for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                            if not chunk:
                                continue
                            handle.write(chunk)
                            progress.update(len(chunk))
            size = Path(tmp_name).stat().st_size
            if size == 0:
                raise ValueError("empty response body")
            os.replace(tmp_name, dest)
            return size
        except (requests.RequestException, OSError, ValueError) as exc:
            last_error = exc
            if fd >= 0:
                os.close(fd)
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            if attempt < retries:
                sleep_for = backoff * (2.0**attempt)
                logger.warning(
                    "%s: download failed (%s); retrying in %.1fs (attempt %d/%d)",
                    name,
                    exc,
                    sleep_for,
                    attempt + 1,
                    retries,
                )
                time.sleep(sleep_for)
    logger.error("%s: giving up after %d attempt(s): %s", name, retries + 1, last_error)
    return None


def _download_category(
    category: str,
    sources: dict[str, str],
    base_dest_dir: Path,
    config: dict[str, str],
    *,
    dry_run: bool,
    delay: float,
) -> tuple[int, int, int]:
    """Download one category and return ``(ok, failed, bytes)``."""
    dest_dir = base_dest_dir / category
    if dry_run:
        print(f"[{category}]")
        for name, url in sources.items():
            print(f"{name}: {url}")
        return 0, 0, 0

    ok_count = 0
    failed_count = 0
    total_bytes = 0
    downloaded_files: list[str] = []
    source_items = list(sources.items())
    for index, (name, url) in enumerate(source_items):
        size = download_source(
            name,
            url,
            dest_dir,
            timeout=float(config["REQUEST_TIMEOUT"]),
            retries=int(config["RETRIES"]),
            backoff=float(config["RETRY_BACKOFF"]),
            user_agent=config["USER_AGENT"],
        )
        if size is None:
            failed_count += 1
        else:
            ok_count += 1
            total_bytes += size
            downloaded_files.append(download_path(name, url, dest_dir).name)
            logger.info("%s/%s: %s downloaded", category, name, utils.human_size(size))
        if delay > 0 and index < len(source_items) - 1:
            time.sleep(delay)

    # The manifest makes automatic merging immune to stale orphaned downloads
    # from sources that were renamed/removed between runs.
    utils.atomic_write_lines(dest_dir / source_catalog.DOWNLOAD_MANIFEST, downloaded_files)
    logger.info(
        "%s: %d/%d sources succeeded, %s total",
        category,
        ok_count,
        ok_count + failed_count,
        utils.human_size(total_bytes),
    )
    if failed_count:
        logger.warning("%s: %d source(s) failed and were skipped", category, failed_count)
    return ok_count, failed_count, total_bytes


class DownloadArgs(argparse.Namespace):
    """Typed arguments for the download CLI (see main())."""

    sources: str | None = None
    output_dir: str | None = None
    category: str = source_catalog.DEFAULT_CATEGORY
    all_categories: bool = False
    dry_run: bool = False
    no_rate_limit: bool = False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ace-hosts-download",
        description="Download category-specific host lists from upstream sources.",
    )
    parser.add_argument(
        "--category",
        choices=source_catalog.category_names(),
        default=source_catalog.DEFAULT_CATEGORY,
        help=f"category to download (default: {source_catalog.DEFAULT_CATEGORY})",
    )
    parser.add_argument("--all-categories", action="store_true", help="download every built-in category")
    parser.add_argument(
        "--sources",
        help="override sources for one category: bare URLs or name=url entries, comma/newline separated",
    )
    parser.add_argument(
        "--output-dir",
        help="base directory for raw category downloads (default: $DOWNLOAD_DIR, i.e. downloads/)",
    )
    parser.add_argument("--dry-run", action="store_true", help="list selected sources and exit")
    parser.add_argument("--no-rate-limit", action="store_true", help="skip the delay between sources")
    args: DownloadArgs = parser.parse_args(argv, namespace=DownloadArgs())

    config = utils.load_config()
    utils.setup_logging(config["LOG_LEVEL"])
    custom_sources = args.sources or config.get("SOURCES") or ""
    if args.all_categories and custom_sources.strip():
        parser.error("--sources/SOURCES cannot be combined with --all-categories")

    base_dest_dir = Path(args.output_dir or config["DOWNLOAD_DIR"])
    if not base_dest_dir.is_absolute():
        base_dest_dir = utils.PROJECT_ROOT / base_dest_dir

    categories = source_catalog.category_names() if args.all_categories else (args.category,)
    delay = 0.0 if args.no_rate_limit else float(config["RATE_LIMIT_DELAY"])
    total_ok = 0
    for category in categories:
        defaults = source_catalog.sources_for_category(category)
        category_sources = parse_sources(custom_sources, defaults)
        if not category_sources:
            logger.error("%s: no sources configured", category)
            continue
        ok_count, _, _ = _download_category(
            category,
            category_sources,
            base_dest_dir,
            config,
            dry_run=args.dry_run,
            delay=delay,
        )
        if args.dry_run:
            continue
        total_ok += ok_count

    if args.dry_run:
        return 0
    if total_ok == 0:
        logger.error("all selected sources failed; nothing to merge")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
