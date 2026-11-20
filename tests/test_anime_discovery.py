"""Offline regression tests for evidence-led anime and novel discovery."""
from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path
from typing import cast

import pytest
import requests

from scripts import anime_discovery as discovery
from scripts import merge_hosts, source_catalog, utils
from scripts.build_site_packs import SITE_PACKS, build_site_pack


@pytest.mark.parametrize(("url", "expected"), [
    ("https://漫画.example.org/chapter?id=1", "xn--qex62k.example.org"),
    ("https://www.mangadex.org:443/title", "www.mangadex.org"),
    ("animeworld.it", "animeworld.it"),
    ("https://site.com:444", None),
    ("https://user:pass@site.com", None),
    ("https://127.0.0.1/robots.txt", None),
    ("https://[::1]/", None),
    ("localhost", None),
    ("name.test", None),
    ("bad string.org", None),
    ("ftp://example.org", None),
    ("https://a.example.org..", None),
    ("https://fake.com@amazon.com", None),
])
def test_idna_and_url_validation(url: str, expected: str | None):
    assert discovery.normalize_hostname(url) == expected


def test_shared_service_guards_preserve_specific_non_shared_hosts():
    assert discovery.is_shared("cdn.amazonaws.com")
    assert discovery.is_shared("github.com")
    assert discovery.is_shared("foo.github.io")
    assert discovery.is_shared("reddit.com")
    assert not discovery.safe_hostname("reddit.com")
    assert discovery.safe_hostname("manga.example.org")
    assert discovery.safe_hostname("read.manga.example.org")


@pytest.mark.parametrize(("adapter", "payload", "expected"), [
    ("tachiyomi", [{"pkg": "test.ext", "sources": [
        {"name": "A", "baseUrl": "https://マンガ.org", "lang": "ja", "id": 42},
        {"name": "CDN", "baseUrl": "", "lang": "all"}]}], "xn--mck5erc.org"),
    ("mangayomi", [{"name": "Scan", "baseUrl": "https://scan.org",
                   "lang": "pt-BR", "itemType": 0, "id": 5,
                   "sourceCodeUrl": "https://github.com/evil"}], "scan.org"),
    ("aidoku", {"sources": [{"name": "Comics", "baseURL": "https://comics.org",
                           "languages": ["zh-Hant", "en"], "id": "comic"}]}, "comics.org"),
    ("miru", [{"name": "Watch", "webSite": "https://anime.org/watch",
               "type": "bangumi", "lang": "it", "package": "watch",
               "icon": "https://cdn.com/icon.png"}], "anime.org"),
    ("lnreader", [{"name": "Novel", "site": "https://novels.org",
                   "lang": "‎العربية", "id": "novel",
                   "url": "https://github.com/code"}], "novels.org"),
    ("kuiti", [{"domain": "manhua.org", "langs": "zh", "names": "Manhua",
               "ecosystems": "mihon|aidoku", "eco_count": 2}], "manhua.org"),
])
def test_catalog_adapters_ignore_non_site_urls(adapter: str, payload: object, expected: str):
    catalog = discovery.Catalog("fixture", "https://example.org/manifest.json",
                                adapter, "family", "MIT")
    records = discovery.parse_catalog(payload, catalog)
    assert len(records) == 1
    assert records[0].hostname == expected
    assert records[0].repository == "fixture"
    if adapter == "lnreader":
        assert records[0].languages == ("ar",)
    if adapter == "mangayomi":
        assert records[0].languages == ("pt",)


def test_unexpected_and_empty_catalogs_are_rejected():
    catalog = discovery.Catalog("fake", "https://example.org/manifest.json",
                                "aidoku", "family", "MIT")
    with pytest.raises(ValueError, match="not an object"):
        discovery.parse_catalog([], catalog)
    with pytest.raises(ValueError, match="invalid catalog structure"):
        discovery.parse_catalog({"sources": {}}, catalog)


def test_cross_ecosystem_quorum_and_single_source_review():
    def evidence(host: str, family: str, repo: str) -> discovery.Evidence:
        return discovery.Evidence(host, "Site", "manga", ("en",), repo, "ext",
                                  family, "baseURL")

    inputs = [
        evidence("mangasite.org", "aidoku", "aidoku"),
        evidence("mangasite.org", "mangayomi", "mangayomi"),
        evidence("single.org", "aidoku", "aidoku"),
        evidence("single.org", "derived", "kuiti"),
        evidence("github.com", "aidoku", "aidoku"),
        evidence("github.com", "miru", "miru"),
        evidence("reddit.com", "aidoku", "aidoku"),
        evidence("reddit.com", "miru", "miru"),
        evidence("manual-manga.org", "aidoku", "aidoku"),
    ]
    ledger, eligible = discovery.classify(
        inputs, {"mangasite.org", "single.org", "github.com", "reddit.com"},
        {"legacy.org"}, {"manual-manga.org"}, {"reddit.com"}
    )
    statuses = {entry["hostname"]: entry["status"] for entry in ledger}
    assert statuses["mangasite.org"] == "validated-candidate"
    assert statuses["single.org"] == "review"
    assert statuses["github.com"] == "excluded"
    assert statuses["reddit.com"] == "excluded"
    assert statuses["manual-manga.org"] == "validated-candidate"
    assert eligible == {"mangasite.org", "manual-manga.org"}


def test_joined_without_review_is_never_published(tmp_path: Path):
    joined = tmp_path / "joined.txt"
    joined.write_text("".join(f"source{i}.org\n" for i in range(25000)) +
                      "confirmed.org\nfacebook.com\n", encoding="utf-8")
    pro = tmp_path / "pro.txt"
    pro.write_text("".join(f"curated{i}.org\n" for i in range(101)), encoding="utf-8")
    approved = tmp_path / "approved.txt"
    approved.write_text("", encoding="utf-8")
    whitelist = tmp_path / "whitelist.txt"
    whitelist.write_text("", encoding="utf-8")
    exceptions = tmp_path / "exceptions.txt"
    exceptions.write_text("", encoding="utf-8")
    evidence = [
        discovery.Evidence("confirmed.org", "Confirmed", "manga", ("en",), repo, "42", family, "baseUrl")
        for family, repo in (("aidoku", "aidoku"), ("mangayomi", "mangayomi"))
    ]
    output = tmp_path / "output"
    summary = discovery.build_discovery(
        evidence, joined_file=joined, pro_file=pro, approved_file=approved,
        whitelist_file=whitelist, exceptions_file=exceptions,
        output_dir=output, checked_at="2026-11-03"
    )
    assert summary["new_eligible"] == 1
    assert list(utils.iter_domains(output / "eligible-anime.txt")) == ["confirmed.org"]
    record = cast(object, json.loads((output / "candidates.jsonl").read_text()))
    assert isinstance(record, dict)
    assert record["repositories"] == ["aidoku", "mangayomi"]
    assert record["detected_at"] == "2026-11-03"


def test_failed_refresh_does_not_publish_partial_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    catalog = discovery.Catalog("mock", "https://example.org/data.json", "mangayomi", "mock", "MIT")
    def fails(*_args: object, **_kwargs: object) -> bytes:
        raise requests.Timeout("offline")
    monkeypatch.setattr(discovery, "_fetch", fails)
    with requests.Session() as session, pytest.raises(requests.Timeout):
        discovery.fetch_catalog(session, catalog, tmp_path, refresh=True)
    assert not (tmp_path / "mock.json").exists()


def test_end_to_end_reviewed_anime_merge_and_pack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(utils, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path / "downloads"))
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "hosts"))
    monkeypatch.setenv("EXCEPTIONS_DIR", str(tmp_path / "exceptions"))
    monkeypatch.setenv("WHITELIST_FILE", str(tmp_path / "whitelist.txt"))
    raw = tmp_path / "downloads" / "anime-manga"
    raw.mkdir(parents=True)
    (raw / "refined.txt").write_text(
        "mangadex.org\nwebtoons.com\n", encoding="utf-8")
    (raw / "newedgex-joined.txt").write_text(
        "".join(f"joined{i}.org\n" for i in range(discovery.MIN_JOINED_DOMAINS)) +
        "reddit.com\nfacebook.com\nconfirmed.org\nunreviewed.org\n", encoding="utf-8")
    (raw / source_catalog.DOWNLOAD_MANIFEST).write_text(
        "refined.txt\nnewedgex-joined.txt\n", encoding="utf-8")
    discovery_dir = tmp_path / "downloads" / "anime-discovery"
    discovery_dir.mkdir()
    (discovery_dir / "eligible-anime.txt").write_text(
        "confirmed.org\nfacebook.com\nunreviewed.org\n", encoding="utf-8")
    approvals_dir = tmp_path / "seeds"
    approvals_dir.mkdir()
    (approvals_dir / "anime-manga.txt").write_text(
        "confirmed.org\nfacebook.com\n", encoding="utf-8")
    assert merge_hosts.main(["--category", "anime-manga", "--no-progress"]) == 0
    generated = tmp_path / "hosts" / "anime-manga.txt"
    assert set(utils.iter_domains(generated)) == {
        "mangadex.org", "webtoons.com", "confirmed.org"}
    definition = next(p for p in SITE_PACKS if p.pack_id == "pack-anime-manga-pro")
    archive = build_site_pack(definition, tmp_path / "hosts", tmp_path / "packs")
    with zipfile.ZipFile(archive) as zip_file:
        assert zip_file.namelist() == ["pack.db"]
        zip_file.extract("pack.db", tmp_path / "pack")
    with sqlite3.connect(tmp_path / "pack" / "pack.db") as connection:
        assert connection.execute("SELECT entry_count FROM pack_meta").fetchone() == (3,)
        assert connection.execute("SELECT id FROM pack_meta").fetchone() == (
            "pack-anime-manga-pro",)


def test_anime_never_falls_back_to_raw_joined(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(utils, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path / "downloads"))
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "hosts"))
    raw = tmp_path / "downloads" / "anime-manga"
    raw.mkdir(parents=True)
    (raw / "newedgex-joined.txt").write_text("facebook.com\n", encoding="utf-8")
    (raw / source_catalog.DOWNLOAD_MANIFEST).write_text("newedgex-joined.txt\n", encoding="utf-8")
    assert merge_hosts.main(["--category", "anime-manga", "--no-progress"]) == 1
    assert not (tmp_path / "hosts" / "anime-manga.txt").exists()

def test_maintainer_approval_without_catalog_record_and_exclusions():
    ledger, eligible = discovery.classify(
        [], set(), set(), {"reviewed-manga.org", "github.com"},
        {"deny-reviewed.org"}
    )
    assert eligible == {"reviewed-manga.org"}
    statuses = {entry["hostname"]: entry["status"] for entry in ledger}
    assert statuses == {
        "github.com": "excluded",
        "reviewed-manga.org": "validated-candidate",
    }


def test_truncated_joined_input_fails_closed(tmp_path: Path):
    paths = {
        name: tmp_path / f"{name}.txt"
        for name in ("joined", "pro", "allowed", "whitelist", "exceptions")
    }
    paths["joined"].write_text("mangasite.org\n", encoding="utf-8")
    paths["pro"].write_text(
        "".join(f"curated{i}.org\n" for i in range(101)), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="unexpectedly small"):
        discovery.build_discovery(
            [], joined_file=paths["joined"], pro_file=paths["pro"],
            approved_file=paths["allowed"], whitelist_file=paths["whitelist"],
            exceptions_file=paths["exceptions"], output_dir=tmp_path / "reports",
            checked_at="2026-11-03"
        )
    assert not (tmp_path / "reports").exists()


def _catalog_homepage_with_pages(host: str, *, pages: int = 8) -> list[discovery.Evidence]:
    """Synthetic successful fetch evidence; no live requests or .temp input."""
    records = [
        discovery.Evidence(host, "Manga catalog", "manga", (), "aidoku", "source",
                           "aidoku", "baseURL"),
        discovery.Evidence(host, "Read manga online", "manga", (), "site-homepage",
                           None, "web-lead", "html-title-description",
                           origin=f"https://{host}/"),
    ]
    records.extend(
        discovery.Evidence(
            host, f"Distinct series number {index}", "manga", (),
            "site-internal-page", None, "web-lead", "structured-content-page",
            origin=f"https://{host}/series/series-{index}",
        )
        for index in range(pages)
    )
    return records


@pytest.mark.parametrize("host", [
    "lightnovel.to", "madarascans.net", "violetmanga.com",
    "nightjarreads.com", "ranobemir.com",
])
def test_catalog_homepage_and_eight_pages_qualify_without_joined(host: str) -> None:
    records = _catalog_homepage_with_pages(host)
    ledger, approved = discovery.classify(records, set(), set(), set(), set())
    row = ledger[0]
    assert approved == set()  # Review proposal, never automatic publication.
    assert row["release_approved"] is False
    assert row["joined"] is False
    assert row["independent_families"] == 1
    assert row["review_eligible"] is True
    assert row["eligibility_path"] == "catalog-verified-content"
    assert row["verification_method"] == "catalog-and-verified-pages"
    assert row["onsite_content_pages"] == 8
    assert row["onsite_distinct_titles"] == 8
    paths = cast(list[dict[str, object]], row["eligibility_paths"])
    assert [path["path_code"] for path in paths] == [
        "independent-catalogs", "catalog-joined-homepage",
        "catalog-verified-content", "web-original",
    ]
    qualified = [path for path in paths if path["qualified"] is True]
    assert [path["path_code"] for path in qualified] == ["catalog-verified-content"]
    required = cast(list[dict[str, object]], qualified[0]["required_checks"])
    assert all(check["required"] is True and check["passed"] is True for check in required)
    assert qualified[0]["path_gaps"] == []
    assert "joined_list_match" in cast(list[str], paths[1]["path_gaps"])
    assert "two_independent_catalog_families" in cast(list[str], paths[0]["path_gaps"])
    assert "no_catalog_family" in cast(list[str], paths[3]["path_gaps"])
    # Changes in evidence enumeration cannot affect the diagnostic path/gaps.
    reversed_ledger, _ = discovery.classify(list(reversed(records)), set(), set(), set(), set())
    assert reversed_ledger[0]["eligibility_path"] == row["eligibility_path"]
    assert reversed_ledger[0]["eligibility_paths"] == row["eligibility_paths"]


def test_independent_and_joined_catalog_paths_remain_available() -> None:
    host = "reader.example.org"
    records = _catalog_homepage_with_pages(host, pages=0)
    joined, published = discovery.classify(records, {host}, set(), set(), set())
    assert joined[0]["eligibility_path"] == "catalog-joined-homepage"
    assert joined[0]["review_eligible"] is True
    assert published == set()

    records.append(discovery.Evidence(host, "Other catalog", "manga", (),
                                      "mangayomi", "source", "mangayomi", "baseUrl"))
    confirmed, published = discovery.classify(records, set(), set(), set(), set())
    assert confirmed[0]["eligibility_path"] == "independent-catalogs"
    assert published == {host}


def test_catalog_content_path_preserves_exclusions_approval_and_pro() -> None:
    host = "reader.example.org"
    records = _catalog_homepage_with_pages(host)
    excluded, approved = discovery.classify(records, set(), set(), {host}, {host})
    assert excluded[0]["eligibility_path"] == "none"
    assert excluded[0]["review_eligible"] is False
    assert excluded[0]["release_approved"] is False
    assert "policy_allowed" in cast(
        list[str],
        cast(list[dict[str, object]], excluded[0]["eligibility_paths"])[2]["path_gaps"],
    )
    assert approved == set()
    pro, approved = discovery.classify(records, set(), {host}, set(), set())
    assert pro[0]["status"] == "pro"
    assert pro[0]["release_approved"] is True
    assert approved == {host}
    seeded, approved = discovery.classify(records, set(), set(), {host}, set())
    assert seeded[0]["release_approved"] is True
    assert approved == {host}


def test_one_url_cannot_provide_two_distinct_content_titles() -> None:
    host = "manga.example.org"
    records = [
        discovery.Evidence(host, "Manga Hub", "manga", (), "aidoku", None,
                           "aidoku", "baseURL"),
        discovery.Evidence(host, "Manga Hub", "manga", (), "site-homepage", None,
                           "web-lead", "html-title-description",
                           origin=f"https://{host}/"),
        discovery.Evidence(host, "Manga First", "manga", (), "site-internal-page",
                           None, "web-lead", "verified-content-page",
                           origin=f"https://{host}/opaque/one"),
        discovery.Evidence(host, "Manga Second", "manga", (), "site-internal-page",
                           None, "web-lead", "verified-content-page",
                           origin=f"https://{host}/opaque/one"),
        discovery.Evidence(host, None, "manga", (), "site-internal-page", None,
                           "web-lead", "verified-content-page",
                           origin=f"https://{host}/opaque/two"),
    ]
    ledger, approved = discovery.classify(records, set(), set(), set(), set())
    assert approved == set()
    row = ledger[0]
    assert row["onsite_content_pages"] == 2
    assert row["onsite_distinct_titles"] == 1
    assert row["review_eligible"] is False


def test_two_structurally_verified_opaque_paths_qualify_with_catalog() -> None:
    host = "manga.example.org"
    records = [
        discovery.Evidence(host, "Manga Hub", "manga", (), "aidoku", None,
                           "aidoku", "baseURL"),
        discovery.Evidence(host, "Manga Hub", "manga", (), "site-homepage", None,
                           "web-lead", "html-title-description",
                           origin=f"https://{host}/"),
        discovery.Evidence(host, "Manga First", "manga", (), "site-internal-page",
                           None, "web-lead", "verified-content-page",
                           origin=f"https://{host}/opaque/one"),
        discovery.Evidence(host, "Manga Second", "manga", (), "site-internal-page",
                           None, "web-lead", "verified-content-page",
                           origin=f"https://{host}/opaque/two"),
    ]
    ledger, approved = discovery.classify(records, set(), set(), set(), set())
    assert approved == set()
    assert ledger[0]["review_eligible"] is True
    assert ledger[0]["eligibility_path"] == "catalog-verified-content"
