"""Tests for scripts/download_sources.py (no network access required)."""

from collections.abc import Iterator
from pathlib import Path

import pytest
import requests

from scripts import download_sources, source_catalog


def test_parse_sources_empty_uses_defaults():
    assert download_sources.parse_sources("") == download_sources.DEFAULT_SOURCES
    assert download_sources.parse_sources("   ") == download_sources.DEFAULT_SOURCES


def test_parse_sources_comma_separated_bare_urls():
    sources = download_sources.parse_sources(
        "https://example.com/a.txt,https://example.com/b.txt"
    )
    assert sources == {
        "a.txt": "https://example.com/a.txt",
        "b.txt": "https://example.com/b.txt",
    }


def test_parse_sources_newline_and_named_entries():
    raw = "one=https://example.com/a.txt\nhttps://example.com/b.txt"
    sources = download_sources.parse_sources(raw)
    assert sources["one"] == "https://example.com/a.txt"
    assert sources["b.txt"] == "https://example.com/b.txt"
    assert len(sources) == 2


def test_parse_sources_mixed_separators():
    raw = "https://example.com/a.txt, two=https://example.com/b.txt\nhttps://example.com/c.txt"
    sources = download_sources.parse_sources(raw)
    assert list(sources) == ["a.txt", "two", "c.txt"]


def test_parse_sources_ignores_empty_entries():
    sources = download_sources.parse_sources("https://example.com/a.txt,,,\n")
    assert sources == {"a.txt": "https://example.com/a.txt"}


def test_parse_sources_sanitizes_named_entries():
    sources = download_sources.parse_sources("../escape=https://example.com/a.txt")
    assert sources == {"_escape": "https://example.com/a.txt"}


def test_default_sources_all_look_like_urls():
    for category in source_catalog.category_names():
        for name, url in source_catalog.sources_for_category(category).items():
            assert url.startswith("https://") or url.startswith("http://"), name
            assert name.strip(), "source names must not be empty"


def test_catalog_contains_product_categories():
    categories = set(source_catalog.category_names())
    assert {"adult", "gaming", "social", "gambling"} <= categories
    assert {"streaming", "dating", "shopping"} <= categories
    assert "porn-only" in source_catalog.sources_for_category("adult")["stevenblack-porn"]


def test_download_path_is_safe_and_does_not_duplicate_extension(tmp_path: Path):
    normal = download_sources.download_path("a.txt", "https://example.com/a.txt", tmp_path)
    escaped = download_sources.download_path("../escape", "https://example.com/a.txt", tmp_path)
    assert normal == tmp_path / "a.txt"
    assert escaped.parent == tmp_path
    assert escaped.name == "_escape.txt"


def test_failed_download_removes_partial_and_stale_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    class BrokenResponse:
        headers: dict[str, str] = {}

        def __enter__(self) -> "BrokenResponse":
            return self

        def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

        def iter_content(self, chunk_size: int) -> Iterator[bytes]:
            del chunk_size
            yield b"partial"
            raise requests.RequestException("connection lost")

    def fake_get(*args: object, **kwargs: object) -> BrokenResponse:
        del args, kwargs
        return BrokenResponse()

    monkeypatch.setattr(requests, "get", fake_get)
    destination = download_sources.download_path("broken", "https://example.com/list.txt", tmp_path)
    destination.write_bytes(b"stale")

    result = download_sources.download_source(
        "broken",
        "https://example.com/list.txt",
        tmp_path,
        timeout=1,
        retries=0,
        backoff=0,
        user_agent="test",
    )

    assert result is None
    assert not destination.exists()
    assert not list(tmp_path.glob("*.tmp"))
