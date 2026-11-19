"""Guard the monthly release contract between Free and Pro packs."""

from pathlib import Path

import pytest

from scripts.validate_tier_coverage import validate_tier_coverage


def test_pro_edition_contains_every_free_domain(tmp_path: Path) -> None:
    (tmp_path / "free.txt").write_text("one.example\ntwo.example\n")
    (tmp_path / "pro.txt").write_text(
        "# comment\n127.0.0.1 one.example\n127.0.0.1 bonus.example\n127.0.0.1 two.example\n",
        encoding="utf-8",
    )
    assert validate_tier_coverage(tmp_path, (("free", "pro"),)) == {
        "free": (2, 3),
    }


def test_rejects_missing_free_domains(tmp_path: Path) -> None:
    (tmp_path / "free.txt").write_text("one.example\ntwo.example\n")
    (tmp_path / "pro.txt").write_text("one.example\n")
    with pytest.raises(ValueError, match="1 Free domains are missing"):
        validate_tier_coverage(tmp_path, (("free", "pro"),))


def test_rejects_missing_or_empty_sources(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="missing Free or Pro"):
        validate_tier_coverage(tmp_path, (("free", "pro"),))
    (tmp_path / "free.txt").write_text("# no hosts\n")
    (tmp_path / "pro.txt").write_text("one.example\n")
    with pytest.raises(ValueError, match="Free list is empty"):
        validate_tier_coverage(tmp_path, (("free", "pro"),))
