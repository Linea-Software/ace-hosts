"""Offline safety checks for release category output validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import source_catalog, utils
from scripts.validate_category_outputs import validate_category, validate_all_categories


def _fixture(tmp_path: Path, *, body: str = "127.0.0.1 alpha.example\n127.0.0.1 beta.example\n") -> tuple[Path, Path, Path, Path]:
    hosts = tmp_path / "hosts"
    downloads = tmp_path / "downloads"
    exceptions = tmp_path / "exceptions"
    hosts.mkdir()
    source = downloads / "anime-manga"
    source.mkdir(parents=True)
    exceptions.mkdir()
    (source / "list.txt").write_text("alpha.example\n", encoding="utf-8")
    (source / source_catalog.DOWNLOAD_MANIFEST).write_text("list.txt\n", encoding="utf-8")
    (hosts / "anime-manga.txt").write_text(
        "\n".join([
            "# Title: ace-hosts anime-manga blocklist",
            "# Category: anime-manga",
            "# Entries: 2",
            "# Source licenses: GPL-3.0",
        ]) + "\n" + body,
        encoding="utf-8",
    )
    whitelist = tmp_path / "whitelist.txt"
    whitelist.write_text("# safe\n", encoding="utf-8")
    return hosts, downloads, exceptions, whitelist


def test_valid_category_verifies_current_sources_and_domain_count(tmp_path: Path):
    hosts, downloads, exceptions, _ = _fixture(tmp_path)
    assert validate_category("anime-manga", hosts, downloads, exceptions, set()) == (2, 1)


@pytest.mark.parametrize(("body", "message"), [
    ("", "empty merged"),
    ("127.0.0.1 alpha.example\n127.0.0.1 alpha.example\n", "unsorted or duplicated"),
    ("127.0.0.1 beta.example\n127.0.0.1 alpha.example\n", "unsorted or duplicated"),
    ("127.0.0.1 alpha.example\nNOT-A-DOMAIN\n", "invalid output line"),
])
def test_rejects_bad_outputs(tmp_path: Path, body: str, message: str):
    hosts, downloads, exceptions, _ = _fixture(tmp_path, body=body)
    with pytest.raises(ValueError, match=message):
        validate_category("anime-manga", hosts, downloads, exceptions, set())


def test_rejects_excluded_hosts_and_stale_download_manifest(tmp_path: Path):
    hosts, downloads, exceptions, _ = _fixture(tmp_path)
    (exceptions / "anime-manga.txt").write_text("alpha.example\n", encoding="utf-8")
    with pytest.raises(ValueError, match="excluded hostname"):
        validate_category("anime-manga", hosts, downloads, exceptions, set())

    (exceptions / "anime-manga.txt").unlink()
    (downloads / "anime-manga" / "list.txt").unlink()
    with pytest.raises(ValueError, match="invalid or missing download"):
        validate_category("anime-manga", hosts, downloads, exceptions, set())


def test_rejects_missing_license_and_wrong_counts(tmp_path: Path):
    hosts, downloads, exceptions, _ = _fixture(tmp_path)
    path = hosts / "anime-manga.txt"
    path.write_text(path.read_text().replace("Entries: 2", "Entries: 10"), encoding="utf-8")
    with pytest.raises(ValueError, match="incorrect entry count"):
        validate_category("anime-manga", hosts, downloads, exceptions, set())
    path.write_text(path.read_text().replace("Entries: 10", "Entries: 2").replace("# Source licenses: GPL-3.0\n", ""), encoding="utf-8")
    with pytest.raises(ValueError, match="missing upstream license"):
        validate_category("anime-manga", hosts, downloads, exceptions, set())


def test_all_category_validation_covers_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    hosts, downloads, exceptions, whitelist = _fixture(tmp_path)
    monkeypatch.setattr(source_catalog, "category_names", lambda: ("anime-manga",))
    results = validate_all_categories(hosts, downloads, exceptions, whitelist)
    assert results["anime-manga"] == (2, 1)


def test_curated_anime_exceptions_reject_generic_news_domain():
    exceptions = set(utils.iter_domains(utils.PROJECT_ROOT / "exceptions" / "anime-manga.txt"))
    assert "nhk.or.jp" in exceptions
