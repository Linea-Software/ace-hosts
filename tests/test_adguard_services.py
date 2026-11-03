"""AdGuard service JSON import must fail closed on unsupported DNS rules."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
import requests

from scripts import adguard_services, download_sources, source_catalog, utils


@pytest.mark.parametrize(("rule", "expected"), [
    ("||ROBLOX.com^", "roblox.com"),
    ("||robloxcdn.com", "robloxcdn.com"),
    ("||hello^", None),
    ("||example.com^$important", None),
    ("@@||example.com^", None),
    ("||*.example.com^", None),
    ("/example.com/", None),
    ("||foo.googleapis.com^", None),
    ("||login.live.com^", None),
    ("||github.io^", None),
    (None, None),
])
def test_simple_safe_hostname_rules_only(rule: object, expected: str | None):
    assert adguard_services.adguard_rule_domain(rule) == expected


def test_group_selection_deduplicates_and_ignores_other_services():
    payload = {"blocked_services": [
        {"group": "gaming", "rules": [
            "||ROBLOX.com^", "||robloxcdn.com^", "||login.live.com^",
        ]},
        {"group": "gaming", "rules": ["||roblox.com^", "||steamcommunity.com"]},
        {"group": "messenger", "rules": ["||discord.com^"]},
    ]}
    assert adguard_services.extract_group_domains(payload, "gaming") == [
        "roblox.com", "robloxcdn.com", "steamcommunity.com",
    ]


@pytest.mark.parametrize("payload", [
    {}, {"blocked_services": {}}, {"blocked_services": []},
    {"blocked_services": [{"group": "gaming", "rules": ["||bad^"]}]},
])
def test_invalid_registry_fails_closed(payload: object):
    with pytest.raises(ValueError):
        adguard_services.extract_group_domains(payload, "gaming")


def test_postprocessing_and_registered_sources(tmp_path: Path):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"blocked_services": [
        {"group": "gaming", "rules": ["||roblox.com^", "||steamcommunity.com^"]},
        {"group": "shopping", "rules": ["||amazon.com^"]},
    ]}), encoding="utf-8")
    adguard_services.normalize_registry_file(path, "gaming")
    assert list(utils.iter_domains(path)) == ["roblox.com", "steamcommunity.com"]
    for category in adguard_services.CATEGORY_GROUPS:
        source = next(item for item in source_catalog.CATEGORY_SOURCES[category]
                      if item.kind == "adguard-services")
        assert source.url == adguard_services.ADGUARD_SERVICES_URL


def test_downloader_converts_registry_before_publishing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    class FakeResponse:
        headers: dict[str, str] = {}
        def __enter__(self) -> "FakeResponse":
            return self
        def __exit__(self, *args: object) -> None:
            return None
        def raise_for_status(self) -> None:
            return None
        def iter_content(self, chunk_size: int) -> Iterator[bytes]:
            del chunk_size
            yield json.dumps({"blocked_services": [
                {"group": "gaming", "rules": ["||roblox.com^", "||steamcommunity.com^"]},
            ]}).encode()

    def fake_get(*args: object, **kwargs: object) -> FakeResponse:
        del args, kwargs
        return FakeResponse()

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path / "downloads"))
    def registry_only(category: str) -> dict[str, str]:
        assert category == "gaming"
        return {"adguard-services.txt": adguard_services.ADGUARD_SERVICES_URL}

    monkeypatch.setattr(source_catalog, "sources_for_category", registry_only)
    assert download_sources.main(["--category", "gaming", "--no-rate-limit"]) == 0
    dest = tmp_path / "downloads" / "gaming"
    assert list(utils.iter_domains(dest / "adguard-services.txt")) == [
        "roblox.com", "steamcommunity.com",
    ]
    assert (dest / source_catalog.DOWNLOAD_MANIFEST).read_text() == "adguard-services.txt\n"


def test_failed_transform_does_not_publish_stale_or_raw_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    class FakeResponse:
        headers: dict[str, str] = {}
        def __enter__(self) -> "FakeResponse":
            return self
        def __exit__(self, *args: object) -> None:
            return None
        def raise_for_status(self) -> None:
            return None
        def iter_content(self, chunk_size: int) -> Iterator[bytes]:
            del chunk_size
            yield b'{"blocked_services": []}'

    def fake_get(*args: object, **kwargs: object) -> FakeResponse:
        del args, kwargs
        return FakeResponse()

    monkeypatch.setattr(requests, "get", fake_get)
    dest = tmp_path / "adguard-services.txt"
    dest.write_text("stale.example\n")
    result = download_sources.download_source(
        "adguard-services.txt", adguard_services.ADGUARD_SERVICES_URL,
        tmp_path, timeout=1, retries=0, backoff=0, user_agent="test",
        postprocess=lambda path: adguard_services.normalize_registry_file(path, "gaming"),
    )
    assert result is None
    assert not dest.exists()
    assert not list(tmp_path.glob("*.tmp"))
