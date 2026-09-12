"""Split a merged hosts file into GitHub-friendly chunks."""

from __future__ import annotations

import argparse
import itertools
import logging
import os
import re
import sys
import tempfile
from pathlib import Path

from tqdm import tqdm

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import utils

logger = logging.getLogger("ace-hosts.split")
HEADER_PREFIX = "#"


def _line_bytes(line: str) -> int:
    """Size of a line on disk, including its trailing newline."""
    return len(line.encode("utf-8")) + 1


def _chunk_name_matches(name: str, prefix: str) -> bool:
    return re.fullmatch(rf"{re.escape(prefix)}\d+", name) is not None


def find_chunks(out_dir: Path, prefix: str) -> list[Path]:
    """Return only sequential-style chunk files for *prefix*."""
    if not out_dir.is_dir():
        return []
    return sorted(path for path in out_dir.iterdir() if path.is_file() and _chunk_name_matches(path.name, prefix))


def split_file(
    source: Path,
    out_dir: Path,
    max_mb: float = 90.0,
    prefix: str = "hosts",
    show_progress: bool = True,
) -> list[Path]:
    """Split *source* into chunks of at most *max_mb* MiB each.

    Chunks are assembled in a temporary directory before publication. This
    prevents a parse/write failure from leaving stale extra chunks mixed with
    a partially rebuilt set. Lines are never split.
    """
    if not source.is_file():
        raise FileNotFoundError(f"input file not found: {source}")
    max_bytes = int(max_mb * 1024 * 1024)
    if max_bytes <= 0:
        raise ValueError("max_mb must be positive")

    header_lines: list[str] = []
    total_lines = 0
    with source.open("r", encoding="utf-8-sig", errors="replace") as handle:
        for line in handle:
            line = line.rstrip("\r\n")
            if not line:
                continue
            if line.startswith(HEADER_PREFIX):
                header_lines.append(line)
            else:
                total_lines += 1
    logger.debug("header: %d line(s), payload: %d line(s)", len(header_lines), total_lines)

    base_header_size = sum(_line_bytes(line) for line in header_lines)
    out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix=f".{prefix}-split-", dir=str(out_dir)) as temp_name:
        staging_dir = Path(temp_name)
        staged_chunks: list[Path] = []
        current: list[str] = []
        current_size = 0

        def flush() -> None:
            nonlocal current_size
            index = len(staged_chunks)
            chunk_path = staging_dir / f"{prefix}{index:02d}"
            utils.atomic_write_lines(
                chunk_path,
                itertools.chain(header_lines, utils.chunk_header(index), current),
            )
            staged_chunks.append(chunk_path)
            logger.info(
                "  %s: %s, %d line(s)",
                chunk_path.name,
                utils.human_size(chunk_path.stat().st_size),
                len(current),
            )
            current.clear()
            current_size = 0

        with source.open("r", encoding="utf-8-sig", errors="replace") as handle:
            iterator = tqdm(handle, total=total_lines, unit="line", desc="splitting", disable=not show_progress)
            for line in iterator:
                line = line.rstrip("\r\n")
                if not line or line.startswith(HEADER_PREFIX):
                    continue
                line_size = _line_bytes(line)
                index = len(staged_chunks)
                chunk_header_size = sum(_line_bytes(item) for item in utils.chunk_header(index))
                if current and base_header_size + chunk_header_size + current_size + line_size > max_bytes:
                    flush()
                    index = len(staged_chunks)
                    chunk_header_size = sum(_line_bytes(item) for item in utils.chunk_header(index))
                if base_header_size + chunk_header_size + line_size > max_bytes:
                    logger.warning(
                        "one line plus headers exceeds the chunk size (%.1f KiB): %s",
                        (base_header_size + chunk_header_size + line_size) / 1024,
                        line[:80],
                    )
                current.append(line)
                current_size += line_size
        if current:
            flush()

        published: list[Path] = []
        expected_names = {chunk.name for chunk in staged_chunks}
        for staged in staged_chunks:
            destination = out_dir / staged.name
            os.replace(staged, destination)
            published.append(destination)

        for stale in find_chunks(out_dir, prefix):
            if stale.name not in expected_names:
                stale.unlink()
                logger.info("removed stale chunk %s", stale.name)

    logger.info("split %s into %d chunk(s) of at most %s", source, len(published), utils.human_size(max_bytes))
    return published


def verify_chunks(chunks: list[Path], max_mb: float) -> bool:
    """Check every chunk is under the size limit and report its size."""
    max_bytes = int(max_mb * 1024 * 1024)
    all_ok = True
    for chunk in chunks:
        size = chunk.stat().st_size
        status = "ok" if size <= max_bytes else "TOO LARGE"
        if size > max_bytes:
            all_ok = False
        logger.info("  %s: %s (%s)", chunk.name, utils.human_size(size), status)
    return all_ok


class SplitArgs(argparse.Namespace):
    """Typed arguments for the split CLI (see main())."""

    input: str | None = None
    output_dir: str | None = None
    max_mb: float | None = None
    prefix: str | None = None
    check: bool = False
    no_progress: bool = False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ace-hosts-split",
        description="Split a merged hosts file into size-bounded chunks.",
    )
    parser.add_argument("--input", metavar="PATH", help="merged hosts file (default: $OUTPUT_DIR/$MERGED_FILENAME)")
    parser.add_argument("--output-dir", metavar="PATH", help="where chunks are written (default: input file's directory)")
    parser.add_argument("--max-mb", type=float, metavar="MIB", help="max chunk size in MiB (default: $SPLIT_CHUNK_MB)")
    parser.add_argument("--prefix", metavar="NAME", help="chunk filename prefix (default: $SPLIT_PREFIX)")
    parser.add_argument("--check", action="store_true", help="verify existing chunks instead of splitting")
    parser.add_argument("--no-progress", action="store_true", help="disable progress bars")
    args: SplitArgs = parser.parse_args(argv, namespace=SplitArgs())

    config = utils.load_config()
    utils.setup_logging(config["LOG_LEVEL"])
    max_mb = args.max_mb if args.max_mb is not None else float(config["SPLIT_CHUNK_MB"])
    prefix = args.prefix or config["SPLIT_PREFIX"]

    if args.check:
        out_dir = Path(args.output_dir) if args.output_dir else Path(config["OUTPUT_DIR"])
        if not out_dir.is_absolute():
            out_dir = utils.PROJECT_ROOT / out_dir
        chunks = find_chunks(out_dir, prefix)
        if not chunks:
            logger.error("no chunks matching %s<digits> found in %s", prefix, out_dir)
            return 1
        return 0 if verify_chunks(chunks, max_mb) else 1

    in_path = Path(args.input) if args.input else Path(config["OUTPUT_DIR"]) / config["MERGED_FILENAME"]
    if not in_path.is_absolute():
        in_path = utils.PROJECT_ROOT / in_path
    if args.output_dir:
        out_dir = Path(args.output_dir)
        if not out_dir.is_absolute():
            out_dir = utils.PROJECT_ROOT / out_dir
    else:
        out_dir = in_path.parent

    chunks = split_file(in_path, out_dir, max_mb=max_mb, prefix=prefix, show_progress=not args.no_progress)
    logger.info("created %d chunk(s) in %s", len(chunks), out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
