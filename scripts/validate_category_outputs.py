"""Validate built-in category outputs before publishing the monthly release."""

from __future__ import annotations

import argparse
import ipaddress
import logging
from pathlib import Path

from scripts import merge_hosts, source_catalog, utils

logger = logging.getLogger("ace-hosts.validate-categories")


def validate_category(
    category: str,
    hosts_dir: Path,
    downloads_dir: Path,
    exceptions_dir: Path,
    global_whitelist: set[str],
) -> tuple[int, int]:
    """Verify one category has a current source manifest and a valid merged list.

    Domains are verified in a single streaming pass; huge lists are not loaded
    into RAM. The download manifest is authoritative about successful sources.
    """
    manifest = downloads_dir / category / source_catalog.DOWNLOAD_MANIFEST
    if not manifest.is_file():
        raise ValueError(f"{category}: no current successful-source manifest")
    source_names = [line.strip() for line in manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not source_names:
        raise ValueError(f"{category}: no successful source downloads")
    if len(source_names) != len(set(source_names)):
        raise ValueError(f"{category}: duplicate successful source names")
    if any(Path(name).name != name or not (downloads_dir / category / name).is_file() for name in source_names):
        raise ValueError(f"{category}: manifest references an invalid or missing download")

    path = hosts_dir / f"{category}.txt"
    if not path.is_file():
        raise ValueError(f"{category}: missing merged hosts output {path}")
    exclusions = global_whitelist | merge_hosts.load_category_exceptions(exceptions_dir, category)
    if category == "anime-manga-extended":
        exclusions |= merge_hosts.load_category_exceptions(exceptions_dir, "anime-manga")
    metadata: dict[str, str] = {}
    previous = ""
    count = 0
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                key, sep, value = stripped[1:].strip().partition(": ")
                if sep:
                    metadata[key] = value
                continue
            tokens = stripped.split()
            if len(tokens) != 2:
                raise ValueError(f"{category}: invalid output line {stripped!r}")
            try:
                ipaddress.ip_address(tokens[0])
            except ValueError as error:
                raise ValueError(f"{category}: invalid output line {stripped!r}") from error
            domain = tokens[1].lower()
            if "." not in domain or not utils.is_valid_domain(domain):
                raise ValueError(f"{category}: invalid output line {stripped!r}")
            if domain in exclusions:
                raise ValueError(f"{category}: excluded hostname leaked into release: {domain}")
            if previous and domain <= previous:
                raise ValueError(f"{category}: unsorted or duplicated hostname {domain}")
            previous = domain
            count += 1

    if count == 0:
        raise ValueError(f"{category}: empty merged hosts file")
    if metadata.get("Category") != category:
        raise ValueError(f"{category}: missing or mismatched category metadata")
    if metadata.get("Entries") != str(count):
        raise ValueError(f"{category}: incorrect entry count header")
    if not metadata.get("Source licenses"):
        raise ValueError(f"{category}: missing upstream license attribution")
    return count, len(source_names)


def validate_all_categories(
    hosts_dir: Path,
    downloads_dir: Path,
    exceptions_dir: Path,
    whitelist_file: Path,
) -> dict[str, tuple[int, int]]:
    """Validate all built-in assets and print bounded coverage information."""
    global_whitelist = merge_hosts.load_whitelist(whitelist_file)
    results: dict[str, tuple[int, int]] = {}
    for category in source_catalog.category_names():
        results[category] = validate_category(
            category, hosts_dir, downloads_dir, exceptions_dir, global_whitelist
        )
        count, sources = results[category]
        logger.info("%s: %d domains from %d successful source(s)", category, count, sources)
    if "anime-manga-extended" in results:
        from scripts.anime_discovery import safe_hostname

        pro = set(utils.iter_domains(hosts_dir / "anime-manga.txt"))
        extended = set(utils.iter_domains(hosts_dir / "anime-manga-extended.txt"))
        if len(pro) < 4000 or len(extended) < len(pro):
            raise ValueError("anime/manga output unexpectedly short")
        if not pro <= extended:
            raise ValueError("Extended must contain every Pro hostname")
        if any(not safe_hostname(domain) for domain in extended):
            raise ValueError("shared infrastructure or reserved domain in Extended")
        if len(extended - pro) > max(3000, len(pro) // 2):
            raise ValueError("anomalous Extended growth requires manual review")
        approved_path = utils.PROJECT_ROOT / "seeds" / "anime-manga-extended.txt"
        if not approved_path.is_file():
            raise ValueError("Extended requires committed, reviewable approvals")
        approved = set(utils.iter_domains(approved_path))
        excluded = global_whitelist | merge_hosts.load_category_exceptions(
            exceptions_dir, "anime-manga-extended"
        ) | merge_hosts.load_category_exceptions(exceptions_dir, "anime-manga")
        expected = pro | {host for host in approved if host not in excluded and safe_hostname(host)}
        if extended != expected:
            raise ValueError("Extended contains unapproved domains or misses approved seeds")
    return results


class ValidateArgs(argparse.Namespace):
    hosts_dir: Path = utils.PROJECT_ROOT / "hosts"
    downloads_dir: Path = utils.PROJECT_ROOT / "downloads"
    exceptions_dir: Path = utils.PROJECT_ROOT / "exceptions"
    whitelist: Path = utils.PROJECT_ROOT / "whitelist.txt"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hosts-dir", type=Path, default=utils.PROJECT_ROOT / "hosts")
    parser.add_argument("--downloads-dir", type=Path, default=utils.PROJECT_ROOT / "downloads")
    parser.add_argument("--exceptions-dir", type=Path, default=utils.PROJECT_ROOT / "exceptions")
    parser.add_argument("--whitelist", type=Path, default=utils.PROJECT_ROOT / "whitelist.txt")
    args = parser.parse_args(argv, namespace=ValidateArgs())
    utils.setup_logging()
    try:
        validate_all_categories(
            args.hosts_dir, args.downloads_dir, args.exceptions_dir, args.whitelist
        )
    except (ValueError, OSError) as error:
        logger.error("category release validation failed: %s", error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
