"""Download host lists from upstream sources.

This is the fetch side of the maintenance pipeline that the archived
columndeeply/hosts repository performed by hand: download the source lists,
then feed them to ``merge_hosts.py`` (which combines the original
``cleanup.sh`` + ``merger.sh`` steps).

Failures are handled gracefully: each source is retried with exponential
backoff, rate limiting is applied between sources, and a source that keeps
failing is skipped (with a warning) instead of aborting the run.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import requests
from tqdm import tqdm

if __package__ in (None, ""):
    # Running directly as a script (python scripts/download_sources.py): make
    # the project root importable so the package import below works in every
    # execution mode (script, module, installed console script).
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import utils

logger = logging.getLogger("ace-hosts.download")

# Default sources, mirroring the kind of lists used by the original
# columndeeply/hosts repo (StevenBlack, blocklistproject, cbuijs, RPiList,
# tiuxo, ...). Override with the SOURCES env var or --sources. Sources that
# disappear are skipped automatically; keep the list up to date via PRs.
DEFAULT_SOURCES: dict[str, str] = {
    "stevenblack": "https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts",
    "blocklistproject-porn": "https://raw.githubusercontent.com/blocklistproject/Lists/master/porn.txt",
    "cbuijs-porn": "https://raw.githubusercontent.com/cbuijs/shallalist/master/porn/domains",
    "4skinskywalker": "https://raw.githubusercontent.com/4skinSkywalker/Anti-Porn-HOSTS-File/master/HOSTS.txt",
    "rplist-porn": "https://raw.githubusercontent.com/RPiList/specials/master/BlocklistLists/Porn.txt",
    "sinfonietta-porn": "https://raw.githubusercontent.com/Sinfonietta/hostfiles/master/porn-hosts",
    "tiuxo-porn": "https://raw.githubusercontent.com/tiuxo/hosts/master/porn",
    "saskuu-porno": "https://raw.githubusercontent.com/saskuu/blocklist/main/porno.txt",
    "purify-porn": "https://raw.githubusercontent.com/CyberPurifyAI/purify/main/Filters/filter/porn/m.txt",
}

CHUNK_SIZE = 64 * 1024


def parse_sources(raw: str) -> dict[str, str]:
    """Parse a SOURCES-style string into an ordered ``{name: url}`` map.

    Entries are separated by commas or newlines. Each entry is either a bare
    URL (name derived from the filename) or ``name=url``.
    """
    if not raw.strip():
        return dict(DEFAULT_SOURCES)
    sources: dict[str, str] = {}
    for entry in re.split(r"[\n,]+", raw.strip()):
        entry = entry.strip()
        if not entry:
            continue
        if "=" in entry:
            name, _, url = entry.partition("=")
            name = name.strip() or utils.sanitize_source_filename(url)
            sources[name] = url.strip()
        else:
            sources[utils.sanitize_source_filename(entry)] = entry
    return sources


def download_source(
    name: str,
    url: str,
    dest_dir: Path,
    timeout: float,
    retries: int,
    backoff: float,
    user_agent: str,
) -> int | None:
    """Download one source into *dest_dir*; return bytes written or None.

    Streams the response to disk with a progress bar and retries with
    exponential backoff on transient failures.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(url.split("?")[0]).suffix or ".txt"
    dest = dest_dir / f"{name}{suffix}"

    headers = {"User-Agent": user_agent}
    last_error: Exception | None = None
    for attempt in range(retries + 1):
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
                    with dest.open("wb") as handle:
                        # requests' stubs type iter_content as Iterator[Any];
                        # it actually yields raw bytes.
                        content = cast(Iterator[bytes], response.iter_content(chunk_size=CHUNK_SIZE))
                        for chunk in content:
                            if not chunk:
                                continue
                            handle.write(chunk)
                            progress.update(len(chunk))
            size = dest.stat().st_size
            if size == 0:
                dest.unlink(missing_ok=True)
                raise ValueError("empty response body")
            return size
        except (requests.RequestException, OSError, ValueError) as exc:
            last_error = exc
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


class DownloadArgs(argparse.Namespace):
    """Typed arguments for the download CLI (see main())."""

    sources: str | None = None
    output_dir: str | None = None
    dry_run: bool = False
    no_rate_limit: bool = False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ace-hosts-download",
        description="Download host lists from upstream sources for merging.",
    )
    parser.add_argument(
        "--sources",
        help="override sources: bare URLs or name=url entries, comma/newline separated (default: built-in list, or the SOURCES env var)",
    )
    parser.add_argument(
        "--output-dir",
        help="directory for raw downloads (default: $DOWNLOAD_DIR, i.e. downloads/)",
    )
    parser.add_argument("--dry-run", action="store_true", help="list sources and exit without downloading")
    parser.add_argument("--no-rate-limit", action="store_true", help="skip the delay between sources")
    args: DownloadArgs = parser.parse_args(argv, namespace=DownloadArgs())

    config = utils.load_config()
    utils.setup_logging(config["LOG_LEVEL"])

    sources = parse_sources(args.sources or config.get("SOURCES") or "")
    if not sources:
        logger.error("no sources configured")
        return 1

    dest_dir = Path(args.output_dir or config["DOWNLOAD_DIR"])
    if not dest_dir.is_absolute():
        dest_dir = utils.PROJECT_ROOT / dest_dir

    if args.dry_run:
        for name, url in sources.items():
            print(f"{name}: {url}")
        return 0

    delay = 0.0 if args.no_rate_limit else float(config["RATE_LIMIT_DELAY"])
    ok_count = 0
    failed_count = 0
    total_bytes = 0
    source_names = list(sources)
    for index, (name, url) in enumerate(sources.items()):
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
            logger.info("%s: %s downloaded", name, utils.human_size(size))
        # Be polite: wait between sources unless rate limiting is disabled.
        if delay > 0 and index < len(source_names) - 1:
            time.sleep(delay)

    logger.info(
        "download complete: %d/%d sources succeeded, %s total",
        ok_count,
        ok_count + failed_count,
        utils.human_size(total_bytes),
    )
    if failed_count:
        logger.warning("%d source(s) failed and were skipped", failed_count)
    if ok_count == 0:
        logger.error("all sources failed; nothing to merge")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
