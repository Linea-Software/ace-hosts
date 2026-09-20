"""Tests for the category merge pipeline and release asset guard."""

from pathlib import Path

import pytest

from scripts import merge_hosts, source_catalog, utils
from scripts.validate_release_assets import (
    GITHUB_RELEASE_MAX_BYTES,
    validate_release_assets,
)


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_merge_files_dedupes_normalizes_and_filters_whitelist(tmp_path: Path):
    source_a = _write(
        tmp_path,
        "a.txt",
        "# source A\n0.0.0.0 alpha.example.com\n127.0.0.1 beta.example.org # comment\n\ngamma.example.net\n",
    )
    source_b = _write(
        tmp_path,
        "b.txt",
        "alpha.example.com\n||delta.example.com^\nBETA.EXAMPLE.ORG.\n",
    )
    source_c = _write(
        tmp_path,
        "c.txt",
        "epsilon.example.io\n0.0.0.0 localhost\n",
    )
    whitelist = _write(
        tmp_path,
        "whitelist.txt",
        "# safe domains\nbeta.example.org\n",
    )

    whitelist_domains = merge_hosts.load_whitelist(whitelist)
    domains, stats = merge_hosts.merge_files(
        [source_a, source_b, source_c],
        whitelist_domains,
        dedupe=True,
    )

    assert sorted(domains) == sorted(
        [
            "alpha.example.com",
            "delta.example.com",
            "gamma.example.net",
            "epsilon.example.io",
        ]
    )
    assert stats["raw_domains"] == 7
    assert stats["whitelisted"] == 1
    assert stats["unique"] == 4


def test_merge_files_without_dedupe_keeps_duplicates(tmp_path: Path):
    source = _write(
        tmp_path,
        "dup.txt",
        "0.0.0.0 example.com\nexample.com\n",
    )
    domains, stats = merge_hosts.merge_files([source], set(), dedupe=False)
    assert domains == ["example.com", "example.com"]
    assert stats["unique"] == 1


def test_category_manifest_prevents_stale_downloads_from_being_merged(
    tmp_path: Path,
):
    downloads = tmp_path / "downloads"
    category_dir = downloads / "gaming"
    current = _write(category_dir, "current.txt", "current.example\n")
    _write(category_dir, "stale.txt", "stale.example\n")
    _write(category_dir, source_catalog.DOWNLOAD_MANIFEST, "current.txt\n")
    config = dict(utils.DEFAULTS)
    config["DOWNLOAD_DIR"] = str(downloads)

    files = merge_hosts.resolve_input_files([], config, "gaming")
    assert files == [current]


def test_default_category_output_names_are_explicit(tmp_path: Path):
    config = dict(utils.DEFAULTS)
    config["OUTPUT_DIR"] = str(tmp_path)
    assert merge_hosts.default_output_path("adult", config) == tmp_path / "adult.txt"
    assert merge_hosts.default_output_path("gaming", config) == tmp_path / "gaming.txt"


def test_full_merge_pipeline_round_trip(tmp_path: Path):
    expected = [f"{index:03d}.example.com" for index in range(30)]
    _write(
        tmp_path,
        "raw-one.txt",
        "\n".join(f"0.0.0.0 {domain}" for domain in expected) + "\n",
    )
    _write(
        tmp_path,
        "raw-two.txt",
        "\n".join(f"||{domain}^" for domain in expected) + "\n",
    )
    out_path = tmp_path / "out" / "merged.txt"

    exit_code = merge_hosts.main(
        [
            "--input",
            str(tmp_path / "raw-*.txt"),
            "--output",
            str(out_path),
            "--no-progress",
        ]
    )

    assert exit_code == 0
    payload = [
        line
        for line in out_path.read_text(encoding="utf-8").splitlines()
        if not line.startswith("#")
    ]
    assert payload == [f"127.0.0.1 {domain}" for domain in expected]


def test_merge_main_no_inputs_returns_error(tmp_path: Path):
    assert (
        merge_hosts.main(
            [
                "--input",
                str(tmp_path / "does-not-exist-*.txt"),
                "--output",
                str(tmp_path / "out.txt"),
                "--no-progress",
            ]
        )
        == 1
    )


def test_release_asset_validator_accepts_files_at_limit(tmp_path: Path):
    asset = tmp_path / "site-pack-test.zip"
    with asset.open("wb") as handle:
        handle.truncate(GITHUB_RELEASE_MAX_BYTES)
    validate_release_assets(tmp_path)


def test_release_asset_validator_rejects_files_over_limit(tmp_path: Path):
    asset = tmp_path / "site-pack-test.zip"
    with asset.open("wb") as handle:
        handle.truncate(GITHUB_RELEASE_MAX_BYTES + 1)

    with pytest.raises(ValueError, match="site-pack-test.zip"):
        validate_release_assets(tmp_path)


def test_utils_atomic_write(tmp_path: Path):
    target = tmp_path / "atomic.txt"
    utils.atomic_write_lines(target, ["a", "b"])
    assert target.read_text(encoding="utf-8") == "a\nb\n"
