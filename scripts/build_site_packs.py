"""Build Ace Engine-compatible SQLite site-pack backups from ace-hosts outputs."""

from __future__ import annotations

import argparse
import sqlite3
import sys
import tempfile
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import xxhash

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import extract_domains

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_HOSTS_DIR = PROJECT_ROOT / "hosts"
INSERT_BATCH_SIZE = 50_000
PACK_FORMAT_VERSION = "1.0"


@dataclass(frozen=True)
class SitePackDefinition:
    pack_id: str
    name: str
    category: str

    @property
    def asset_name(self) -> str:
        return f"site-pack-{self.pack_id.removeprefix('pack-')}.zip"


SITE_PACKS: tuple[SitePackDefinition, ...] = (
    SitePackDefinition("pack-adult-free", "Adult Websites — Free", "adult"),
    SitePackDefinition("pack-adult-pro", "Adult Websites — Pro", "adult-extended"),
    SitePackDefinition("pack-social-free", "Social Media Websites — Free", "social"),
    SitePackDefinition("pack-gaming-free", "Gaming Websites — Free", "gaming"),
    SitePackDefinition("pack-gambling-pro", "Gambling Websites — Pro", "gambling"),
    SitePackDefinition("pack-streaming-pro", "Streaming Websites — Pro", "streaming"),
    SitePackDefinition("pack-shopping-pro", "Shopping Websites — Pro", "shopping"),
)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS entries (
    host_hash INTEGER PRIMARY KEY NOT NULL
);

CREATE TABLE IF NOT EXISTS pack_meta (
    id TEXT PRIMARY KEY NOT NULL,
    format_version TEXT NOT NULL,
    name TEXT,
    entry_count INTEGER NOT NULL DEFAULT 0,
    built_at TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS pack_meta_validate_entry_count_on_insert
AFTER INSERT ON pack_meta
WHEN NEW.entry_count <> (SELECT COUNT(*) FROM entries)
BEGIN
    SELECT RAISE(ABORT, 'pack_meta.entry_count must equal the entries row count');
END;

CREATE TRIGGER IF NOT EXISTS pack_meta_validate_entry_count_on_update
AFTER UPDATE OF entry_count ON pack_meta
WHEN NEW.entry_count <> (SELECT COUNT(*) FROM entries)
BEGIN
    SELECT RAISE(ABORT, 'pack_meta.entry_count must equal the entries row count');
END;
"""


def xxh3_host_hash(host: str) -> int:
    """Return the signed SQLite INTEGER representation of Ace's XXH3-64 hash."""
    value = xxhash.xxh3_64_intdigest(host.encode("utf-8"))
    return value if value < 2**63 else value - 2**64


def iter_host_hashes(hosts_file: Path) -> Iterable[int]:
    """Yield Ace-compatible hashes for every valid domain in a merged hosts file."""
    with hosts_file.open("r", encoding="utf-8-sig", errors="replace") as handle:
        for line in handle:
            for domain in extract_domains(line):
                yield xxh3_host_hash(domain)


def build_pack_database(
    definition: SitePackDefinition,
    hosts_file: Path,
    db_path: Path,
    *,
    built_at: str | None = None,
) -> int:
    """Build one pack.db and return its unique entry count."""
    if not hosts_file.is_file():
        raise FileNotFoundError(f"missing merged hosts file: {hosts_file}")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    connection = sqlite3.connect(db_path)
    try:
        connection.executescript(_SCHEMA_SQL)
        connection.execute("PRAGMA synchronous = OFF")
        connection.execute("PRAGMA journal_mode = MEMORY")

        batch: list[tuple[int]] = []
        for host_hash in iter_host_hashes(hosts_file):
            batch.append((host_hash,))
            if len(batch) >= INSERT_BATCH_SIZE:
                connection.executemany(
                    "INSERT OR IGNORE INTO entries(host_hash) VALUES (?)",
                    batch,
                )
                batch.clear()
        if batch:
            connection.executemany(
                "INSERT OR IGNORE INTO entries(host_hash) VALUES (?)",
                batch,
            )

        count_row = cast(
            tuple[int],
            connection.execute("SELECT COUNT(*) FROM entries").fetchone(),
        )
        entry_count = count_row[0]
        timestamp = built_at or datetime.now(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
        connection.execute(
            """
            INSERT INTO pack_meta(id, format_version, name, entry_count, built_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                definition.pack_id,
                PACK_FORMAT_VERSION,
                definition.name,
                entry_count,
                timestamp,
            ),
        )
        connection.commit()
    finally:
        connection.close()

    return entry_count


def build_site_pack(
    definition: SitePackDefinition,
    hosts_dir: Path = DEFAULT_HOSTS_DIR,
    output_dir: Path = DEFAULT_HOSTS_DIR,
) -> Path:
    """Build one release ZIP containing a single Ace-compatible pack.db."""
    hosts_file = hosts_dir / f"{definition.category}.txt"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / definition.asset_name

    # Keep the staging ZIP on the destination filesystem so replace() remains
    # an atomic publication even when the system temp directory is elsewhere.
    with tempfile.TemporaryDirectory(prefix="ace-hosts-site-pack-", dir=output_dir) as temp_dir:
        db_path = Path(temp_dir) / "pack.db"
        build_pack_database(definition, hosts_file, db_path)

        temp_zip = Path(temp_dir) / definition.asset_name
        with zipfile.ZipFile(
            temp_zip,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
            allowZip64=True,
        ) as archive:
            archive.write(db_path, arcname="pack.db")
        temp_zip.replace(output_path)

    return output_path


def build_all_site_packs(
    hosts_dir: Path = DEFAULT_HOSTS_DIR,
    output_dir: Path = DEFAULT_HOSTS_DIR,
) -> list[Path]:
    """Build every Ace-managed site pack."""
    return [
        build_site_pack(definition, hosts_dir, output_dir)
        for definition in SITE_PACKS
    ]


class BuildSitePackArgs(argparse.Namespace):
    hosts_dir: Path = DEFAULT_HOSTS_DIR
    output_dir: Path = DEFAULT_HOSTS_DIR
    packs: list[str] | None = None


def parse_args(argv: list[str] | None = None) -> BuildSitePackArgs:
    parser = argparse.ArgumentParser(
        description="Build Ace Engine-compatible SQLite site-pack ZIPs."
    )
    parser.add_argument(
        "--hosts-dir",
        type=Path,
        default=DEFAULT_HOSTS_DIR,
        help="Directory containing merged <category>.txt files.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_HOSTS_DIR,
        help="Directory for site-pack-*.zip release artifacts.",
    )
    parser.add_argument(
        "--pack",
        action="append",
        dest="packs",
        help="Build only the given pack id; repeatable.",
    )
    return parser.parse_args(argv, namespace=BuildSitePackArgs())


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    definitions = SITE_PACKS
    if args.packs:
        requested = set(args.packs)
        definitions = tuple(
            definition
            for definition in SITE_PACKS
            if definition.pack_id in requested
        )
        missing = requested - {definition.pack_id for definition in definitions}
        if missing:
            raise SystemExit(
                f"unknown site pack id(s): {', '.join(sorted(missing))}"
            )

    for definition in definitions:
        output = build_site_pack(definition, args.hosts_dir, args.output_dir)
        print(f"{definition.pack_id}: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
