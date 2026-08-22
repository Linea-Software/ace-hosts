"""Tests for scripts/download_sources.py (no network access required)."""

from scripts import download_sources


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


def test_default_sources_all_look_like_urls():
    for name, url in download_sources.DEFAULT_SOURCES.items():
        assert url.startswith("https://") or url.startswith("http://"), name
        assert name.strip(), "source names must not be empty"
