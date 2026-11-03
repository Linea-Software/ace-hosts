"""Anime, manga, manhwa and manhua pipeline integration tests (offline)."""

from __future__ import annotations

import sqlite3
import zipfile
from pathlib import Path
from typing import cast

import pytest

from scripts import merge_hosts, source_catalog, utils
from scripts.build_site_packs import SITE_PACKS, build_site_pack


def test_anime_manga_sources_and_pack_are_registered():
    sources = source_catalog.sources_for_category("anime-manga")
    assert {"ut1-manga", "newedgex-ani-manga"} <= set(sources)
    definitions = [pack for pack in SITE_PACKS if pack.category == "anime-manga"]
    assert [(pack.pack_id, pack.asset_name) for pack in definitions] == [
        ("pack-anime-manga-pro", "site-pack-anime-manga-pro.zip")
    ]


def test_curated_seeds_cover_multiple_reading_formats():
    seeds = set(utils.iter_domains(utils.PROJECT_ROOT / "seeds" / "anime-manga.txt"))
    assert {
        "anilist.co", "mangadex.org", "webtoons.com",
        "kuaikanmanhua.com", "wuxiaworld.com",
    } <= seeds
    assert "cloudfront.net" not in seeds


def test_merge_includes_seeds_and_category_specific_exceptions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    download = tmp_path / "downloads" / "anime-manga"
    download.mkdir(parents=True)
    (download / "ut1.txt").write_text(
        "mangadex.org\ncommunity.example\naka.ms\n", encoding="utf-8"
    )
    (download / source_catalog.DOWNLOAD_MANIFEST).write_text("ut1.txt\n", encoding="utf-8")
    seeds = tmp_path / "seeds"
    seeds.mkdir()
    (seeds / "anime-manga.txt").write_text(
        "webtoons.com\ncommunity.example\n", encoding="utf-8"
    )
    exceptions = tmp_path / "exceptions"
    exceptions.mkdir()
    (exceptions / "anime-manga.txt").write_text("aka.ms\n", encoding="utf-8")
    monkeypatch.setattr(utils, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path / "downloads"))
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "hosts"))
    monkeypatch.setenv("EXCEPTIONS_DIR", str(exceptions))
    monkeypatch.setenv("WHITELIST_FILE", str(tmp_path / "whitelist.txt"))

    assert merge_hosts.main(["--category", "anime-manga", "--no-progress"]) == 0
    hosts = tmp_path / "hosts" / "anime-manga.txt"
    entries = set(utils.iter_domains(hosts))
    assert entries == {"mangadex.org", "webtoons.com", "community.example"}
    assert "GPL-3.0" in hosts.read_text(encoding="utf-8")

    definition = next(pack for pack in SITE_PACKS if pack.category == "anime-manga")
    result = build_site_pack(definition, tmp_path / "hosts", tmp_path / "release")
    with zipfile.ZipFile(result) as archive:
        archive.extract("pack.db", tmp_path / "extracted")
    with sqlite3.connect(tmp_path / "extracted" / "pack.db") as conn:
        count = cast(tuple[int], conn.execute("SELECT COUNT(*) FROM entries").fetchone())[0]
    assert count == len(entries)


def test_explicit_inputs_do_not_implicitly_append_curated_seeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(utils, "PROJECT_ROOT", tmp_path)
    seeds = tmp_path / "seeds"
    seeds.mkdir()
    (seeds / "anime-manga.txt").write_text("not-requested.example\n", encoding="utf-8")
    source = tmp_path / "external.txt"
    source.write_text("only-requested.example\n", encoding="utf-8")
    output = tmp_path / "output.txt"
    assert merge_hosts.main([
        "--category", "anime-manga", "--input", str(source),
        "--output", str(output), "--no-progress",
    ]) == 0
    assert set(utils.iter_domains(output)) == {"only-requested.example"}
