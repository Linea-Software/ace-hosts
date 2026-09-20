"""Validate generated release assets against GitHub's per-file size limit."""

from __future__ import annotations

import argparse
from pathlib import Path

GITHUB_RELEASE_MAX_BYTES = 2 * 1024 * 1024 * 1024


def oversized_assets(directory: Path, max_bytes: int = GITHUB_RELEASE_MAX_BYTES) -> list[Path]:
    """Return generated files whose size exceeds *max_bytes*."""
    if not directory.is_dir():
        raise FileNotFoundError(f"release asset directory not found: {directory}")
    return sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.stat().st_size > max_bytes
    )


def validate_release_assets(
    directory: Path,
    max_bytes: int = GITHUB_RELEASE_MAX_BYTES,
) -> None:
    """Raise ValueError if any release asset is larger than the allowed size."""
    oversized = oversized_assets(directory, max_bytes=max_bytes)
    if not oversized:
        return

    details = ", ".join(
        f"{path.name} ({path.stat().st_size} bytes)" for path in oversized
    )
    raise ValueError(
        f"release asset size limit exceeded ({max_bytes} bytes): {details}"
    )


class ReleaseAssetArgs(argparse.Namespace):
    directory: Path = Path("hosts")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail if any generated release asset exceeds 2 GiB."
    )
    parser.add_argument(
        "directory",
        nargs="?",
        type=Path,
        default=Path("hosts"),
        help="directory containing release assets (default: hosts)",
    )
    args: ReleaseAssetArgs = parser.parse_args(
        argv,
        namespace=ReleaseAssetArgs(),
    )

    try:
        validate_release_assets(args.directory)
    except (FileNotFoundError, ValueError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
