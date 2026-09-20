"""Tests for Ace Engine-compatible SQLite site-pack generation."""

from __future__ import annotations

import sqlite3
import zipfile
from pathlib import Path
from typing import cast

from scripts.build_site_packs import (
    SitePackDefinition,
    build_pack_database,
    build_site_pack,
    xxh3_host_hash,
)


def test_xxh3_hash_matches_engine_xxh3_64_convention():
    assert xxh3_host_hash("hello") == -7685981735718036227


def test_build_pack_database_matches_engine_schema(tmp_path: Path):
    hosts_file = tmp_path / "adult.txt"
    hosts_file.write_text(
        "\n".join(
            [
                "# generated",
                "127.0.0.1 example.com",
                "127.0.0.1 second.example",
                "127.0.0.1 example.com",
                "",
            ]
        ),
        encoding="utf-8",
    )
    db_path = tmp_path / "pack.db"
    definition = SitePackDefinition(
        "pack-adult-free",
        "Adult Websites — Free",
        "adult",
    )

    count = build_pack_database(
        definition,
        hosts_file,
        db_path,
        built_at="2026-09-20T00:00:00Z",
    )

    assert count == 2
    connection = sqlite3.connect(db_path)
    try:
        hash_rows = cast(
            list[tuple[int]],
            connection.execute(
                "SELECT host_hash FROM entries ORDER BY host_hash"
            ).fetchall(),
        )
        hashes = {row[0] for row in hash_rows}
        metadata = cast(
            tuple[str, str, str, int, str],
            connection.execute(
                "SELECT id, format_version, name, entry_count, built_at FROM pack_meta"
            ).fetchone(),
        )
    finally:
        connection.close()

    assert hashes == {
        xxh3_host_hash("example.com"),
        xxh3_host_hash("second.example"),
    }
    assert metadata == (
        "pack-adult-free",
        "1.0",
        "Adult Websites — Free",
        2,
        "2026-09-20T00:00:00Z",
    )


def test_build_site_pack_is_one_unsplit_zip_with_pack_db(tmp_path: Path):
    hosts_dir = tmp_path / "hosts"
    output_dir = tmp_path / "release"
    hosts_dir.mkdir()
    (hosts_dir / "gaming.txt").write_text(
        "127.0.0.1 game.example\n127.0.0.1 play.example\n",
        encoding="utf-8",
    )
    definition = SitePackDefinition(
        "pack-gaming-free",
        "Gaming Websites — Free",
        "gaming",
    )

    output = build_site_pack(definition, hosts_dir, output_dir)

    assert output.name == "site-pack-gaming-free.zip"
    with zipfile.ZipFile(output) as archive:
        assert archive.namelist() == ["pack.db"]
        archive.extract("pack.db", path=tmp_path / "extracted")

    connection = sqlite3.connect(tmp_path / "extracted" / "pack.db")
    try:
        count = cast(
            tuple[int],
            connection.execute("SELECT COUNT(*) FROM entries").fetchone(),
        )[0]
        pack_id = cast(
            tuple[str],
            connection.execute("SELECT id FROM pack_meta").fetchone(),
        )[0]
    finally:
        connection.close()

    assert count == 2
    assert pack_id == "pack-gaming-free"
