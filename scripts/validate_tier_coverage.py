"""Require full Pro blocklists to include all domains in their Free editions.

The monthly release pipeline runs this check before packaging. We only hold
the smaller Free set in memory; Pro is streamed, so adult-extended remains
safe to validate even with millions of domains.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from scripts import utils

logger = logging.getLogger("ace-hosts.validate-tiers")

TIER_PAIRS: tuple[tuple[str, str], ...] = (
    ("adult", "adult-extended"),
    ("gambling-starter", "gambling"),
    ("streaming-starter", "streaming"),
)


def validate_tier_coverage(
    hosts_dir: Path,
    pairs: tuple[tuple[str, str], ...] = TIER_PAIRS,
) -> dict[str, tuple[int, int]]:
    results: dict[str, tuple[int, int]] = {}
    for free_name, pro_name in pairs:
        free_path = hosts_dir / f"{free_name}.txt"
        pro_path = hosts_dir / f"{pro_name}.txt"
        if not free_path.is_file() or not pro_path.is_file():
            raise ValueError(f"{free_name}/{pro_name}: missing Free or Pro list")

        free_domains = set(utils.iter_domains(free_path))
        if not free_domains:
            raise ValueError(f"{free_name}: Free list is empty")
        missing = free_domains.copy()
        pro_count = 0
        for domain in utils.iter_domains(pro_path):
            missing.discard(domain)
            pro_count += 1

        if missing:
            examples = ", ".join(sorted(missing)[:5])
            raise ValueError(
                f"{pro_name}: {len(missing)} Free domains are missing (examples: {examples})"
            )
        results[free_name] = (len(free_domains), pro_count)
        logger.info(
            "%s: all %d Free domains are present in Pro (%d entries)",
            free_name, len(free_domains), pro_count,
        )
    return results


class ValidateTierArgs(argparse.Namespace):
    hosts_dir: Path = utils.PROJECT_ROOT / "hosts"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hosts-dir", type=Path, default=utils.PROJECT_ROOT / "hosts")
    args = parser.parse_args(argv, namespace=ValidateTierArgs())
    utils.setup_logging()
    try:
        validate_tier_coverage(args.hosts_dir)
    except (ValueError, OSError) as error:
        logger.error("Free/Pro coverage validation failed: %s", error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
