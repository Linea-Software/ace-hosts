"""End-to-end tests for the merge -> split pipeline."""

from pathlib import Path

from scripts import merge_hosts, source_catalog, split_hosts, utils


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
    source_c = _write(tmp_path, "c.txt", "epsilon.example.io\n0.0.0.0 localhost\n")
    whitelist = _write(tmp_path, "whitelist.txt", "# safe domains\nbeta.example.org\n")

    whitelist_domains = merge_hosts.load_whitelist(whitelist)
    domains, stats = merge_hosts.merge_files([source_a, source_b, source_c], whitelist_domains, dedupe=True)

    assert sorted(domains) == sorted(
        ["alpha.example.com", "delta.example.com", "gamma.example.net", "epsilon.example.io"]
    )
    assert stats["raw_domains"] == 7
    assert stats["whitelisted"] == 1
    assert stats["unique"] == 4


def test_merge_files_without_dedupe_keeps_duplicates(tmp_path: Path):
    source = _write(tmp_path, "dup.txt", "0.0.0.0 example.com\nexample.com\n")
    domains, stats = merge_hosts.merge_files([source], set(), dedupe=False)
    assert domains == ["example.com", "example.com"]
    assert stats["unique"] == 1


def test_category_manifest_prevents_stale_downloads_from_being_merged(tmp_path: Path):
    downloads = tmp_path / "downloads"
    category_dir = downloads / "gaming"
    current = _write(category_dir, "current.txt", "current.example\n")
    _write(category_dir, "stale.txt", "stale.example\n")
    _write(category_dir, source_catalog.DOWNLOAD_MANIFEST, "current.txt\n")
    config = dict(utils.DEFAULTS)
    config["DOWNLOAD_DIR"] = str(downloads)

    files = merge_hosts.resolve_input_files([], config, "gaming")
    assert files == [current]


def test_default_category_output_names_preserve_adult_compatibility(tmp_path: Path):
    config = dict(utils.DEFAULTS)
    config["OUTPUT_DIR"] = str(tmp_path)
    assert merge_hosts.default_output_path("adult", config) == tmp_path / "merged_hosts.txt"
    assert merge_hosts.default_split_prefix("adult", config) == "hosts"
    assert merge_hosts.default_output_path("gaming", config) == tmp_path / "gaming.txt"
    assert merge_hosts.default_split_prefix("gaming", config) == "gaming-hosts"


def test_split_file_produces_small_sequential_chunks(tmp_path: Path):
    domains = [f"{i:04d}.example.com" for i in range(40)]
    merged = _write(
        tmp_path,
        "merged.txt",
        "\n".join(["# Title: test", "# Entries: 40", *[f"127.0.0.1 {domain}" for domain in domains]]) + "\n",
    )

    max_mb = 0.0005
    chunks = split_hosts.split_file(merged, tmp_path / "out", max_mb=max_mb, prefix="hosts", show_progress=False)

    assert len(chunks) > 1
    assert len(chunks) <= 5
    assert [chunk.name for chunk in chunks] == [f"hosts{index:02d}" for index in range(len(chunks))]
    for chunk in chunks:
        assert chunk.stat().st_size <= int(max_mb * 1024 * 1024), f"{chunk.name} exceeds chunk size"

    expected_payload = [line for line in merged.read_text().splitlines() if not line.startswith("#")]
    actual_payload: list[str] = []
    for chunk in chunks:
        for line in chunk.read_text().splitlines():
            if not line.startswith("#"):
                actual_payload.append(line)
    assert actual_payload == expected_payload


def test_split_file_removes_stale_old_chunks_only(tmp_path: Path):
    merged = _write(tmp_path, "merged.txt", "127.0.0.1 example.com\n")
    out_dir = tmp_path / "out"
    _write(out_dir, "hosts00", "old\n")
    _write(out_dir, "hosts01", "old\n")
    _write(out_dir, "hosts99", "old\n")
    unrelated = _write(out_dir, "hosts-not-a-chunk", "keep\n")

    chunks = split_hosts.split_file(merged, out_dir, max_mb=1, prefix="hosts", show_progress=False)

    assert [chunk.name for chunk in chunks] == ["hosts00"]
    assert not (out_dir / "hosts01").exists()
    assert not (out_dir / "hosts99").exists()
    assert unrelated.is_file()


def test_find_chunks_requires_numeric_suffix(tmp_path: Path):
    _write(tmp_path, "hosts00", "a\n")
    _write(tmp_path, "hosts01", "a\n")
    _write(tmp_path, "hosts-checksum", "a\n")
    assert [path.name for path in split_hosts.find_chunks(tmp_path, "hosts")] == ["hosts00", "hosts01"]


def test_split_file_zero_padding_grows_beyond_two_digits(tmp_path: Path):
    domains = [f"{i:03d}.example.com" for i in range(15)]
    merged = _write(tmp_path, "merged.txt", "\n".join(f"127.0.0.1 {domain}" for domain in domains) + "\n")
    chunks = split_hosts.split_file(merged, tmp_path / "out", max_mb=0.00001, prefix="hosts", show_progress=False)
    assert len(chunks) == 15
    assert chunks[9].name == "hosts09"
    assert chunks[10].name == "hosts10"


def test_split_file_missing_input_raises(tmp_path: Path):
    try:
        split_hosts.split_file(tmp_path / "nope.txt", tmp_path, max_mb=1)
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("expected FileNotFoundError")


def test_full_pipeline_round_trip(tmp_path: Path):
    expected = [f"{index:03d}.example.com" for index in range(30)]
    _write(tmp_path, "raw-one.txt", "\n".join(f"0.0.0.0 {domain}" for domain in expected) + "\n")
    _write(tmp_path, "raw-two.txt", "\n".join(f"||{domain}^" for domain in expected) + "\n")
    out_dir = tmp_path / "out"

    exit_code = merge_hosts.main(
        [
            "--input",
            str(tmp_path / "raw-*.txt"),
            "--output",
            str(out_dir / "merged.txt"),
            "--split",
            "--split-mb",
            "0.0005",
            "--no-progress",
        ]
    )
    assert exit_code == 0
    merged = out_dir / "merged.txt"
    assert merged.is_file()

    chunks = split_hosts.find_chunks(out_dir, "hosts")
    assert len(chunks) > 1
    assert all(chunk.stat().st_size <= 524 for chunk in chunks)

    payload = [line for line in merged.read_text().splitlines() if not line.startswith("#")]
    assert payload == [f"127.0.0.1 {domain}" for domain in expected]


def test_merge_main_no_inputs_returns_error(tmp_path: Path):
    assert (
        merge_hosts.main(
            ["--input", str(tmp_path / "does-not-exist-*.txt"), "--output", str(tmp_path / "out.txt"), "--no-progress"]
        )
        == 1
    )


def test_verify_chunks_reports_oversized_chunks(tmp_path: Path):
    chunk = _write(tmp_path, "hosts00", "# test\n127.0.0.1 example.com\n")
    assert split_hosts.verify_chunks([chunk], max_mb=0.00001) is False
    assert split_hosts.verify_chunks([chunk], max_mb=1) is True


def test_utils_atomic_write(tmp_path: Path):
    target = tmp_path / "atomic.txt"
    utils.atomic_write_lines(target, ["a", "b"])
    assert target.read_text() == "a\nb\n"
