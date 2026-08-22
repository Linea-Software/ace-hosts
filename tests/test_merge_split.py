"""End-to-end tests for the merge → split pipeline.

Uses synthetic sources (all hosts/adblock/plain-domain formats) and a tiny
chunk size so the splitting logic is exercised without large fixtures.
"""

from pathlib import Path

from scripts import merge_hosts, split_hosts, utils


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
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

    # alpha duplicated across a/b; beta normalized then whitelisted;
    # localhost reserved; gamma/delta/epsilon unique. (merge_files does not
    # sort — sorting happens in main() — so compare sorted.)
    assert sorted(domains) == sorted(
        ["alpha.example.com", "delta.example.com", "gamma.example.net", "epsilon.example.io"]
    )
    assert stats["raw_domains"] == 7  # localhost is dropped as reserved
    assert stats["whitelisted"] == 1
    assert stats["unique"] == 4


def test_merge_files_without_dedupe_keeps_duplicates(tmp_path: Path):
    source = _write(tmp_path, "dup.txt", "0.0.0.0 example.com\nexample.com\n")
    domains, stats = merge_hosts.merge_files([source], set(), dedupe=False)
    assert domains == ["example.com", "example.com"]
    assert stats["unique"] == 1


def test_split_file_produces_small_sequential_chunks(tmp_path: Path):
    domains = [f"{i:04d}.example.com" for i in range(40)]
    merged = _write(
        tmp_path,
        "merged.txt",
        "\n".join(["# Title: test", "# Entries: 40", *[f"127.0.0.1 {domain}" for domain in domains]]) + "\n",
    )

    max_mb = 0.0005  # ~524 bytes → forces several chunks
    chunks = split_hosts.split_file(merged, tmp_path / "out", max_mb=max_mb, prefix="hosts", show_progress=False)

    assert len(chunks) > 1
    # Regression guard: chunks must be packed near the size limit, not one
    # line per chunk. 40 lines x ~30 B + header ~= 1.2 KB / 524 B → 2-3
    # chunks. (A missing current_size reset produced 40+ chunks.)
    assert len(chunks) <= 5
    # Sequential naming with zero padding (hosts00, hosts01, ...).
    assert [chunk.name for chunk in chunks] == [f"hosts{index:02d}" for index in range(len(chunks))]

    for chunk in chunks:
        assert chunk.stat().st_size <= int(max_mb * 1024 * 1024), f"{chunk.name} exceeds chunk size"

    # Reconstructing the chunk payloads must reproduce the merged file
    # exactly (header comments repeat on every chunk).
    expected_payload = [line for line in merged.read_text().splitlines() if not line.startswith("#")]
    actual_payload: list[str] = []
    for chunk in chunks:
        for line in chunk.read_text().splitlines():
            if not line.startswith("#"):
                actual_payload.append(line)
    assert actual_payload == expected_payload


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
    """merge_hosts.main() with --split produces valid chunks from raw lists."""
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

    chunks = sorted(out_dir.glob("hosts*"))
    assert len(chunks) > 1
    assert all(chunk.stat().st_size <= 524 for chunk in chunks)

    payload = [line for line in merged.read_text().splitlines() if not line.startswith("#")]
    assert payload == [f"127.0.0.1 {domain}" for domain in expected]


def test_merge_main_no_inputs_returns_error(tmp_path: Path):
    # An explicit --input that matches nothing must fail fast (without it,
    # auto-discovery would pick up any files in downloads/).
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
