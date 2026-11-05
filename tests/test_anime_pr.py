"""Offline tests for homepage scraping, PR proposals and approval gating."""
from __future__ import annotations

import json
from pathlib import Path
import pytest
import requests

from scripts import anime_discovery as discovery
from scripts import merge_hosts, source_catalog, utils
from scripts.propose_anime_sites import propose_sites


def test_scraping_public_site_metadata_and_links(monkeypatch: pytest.MonkeyPatch):
    def public_host(_host: str) -> bool:
        return True

    monkeypatch.setattr(discovery, "_public_dns_host", public_host)
    urls: list[str] = []

    def fake_fetch(_session: requests.Session, url: str, **_kwargs: object) -> bytes:
        urls.append(url)
        if url.endswith("/robots.txt"):
            return b"User-agent: *\nAllow: /\n"
        return (
            b'<html lang="it"><head><title>Manga Reader</title>'
            b'<meta name="description" content="Anime, manga, manhwa"></head>'
            b'<body><a href="https://linked.org/catalog">link</a>'
            b'<a href="https://github.com/track">track</a></body></html>'
        )

    monkeypatch.setattr(discovery, "_fetch", fake_fetch)
    with requests.Session() as session:
        results = discovery.scrape_site(session, "readmanga.org", {"linked.org"})
    assert urls == [
        "https://readmanga.org/robots.txt",
        "https://readmanga.org/",
        "https://readmanga.org/sitemap.xml",
    ]
    pages = [item for item in results if item.repository == "site-homepage"]
    assert pages
    assert {item.category for item in pages} == {"anime", "manga", "manhwa"}
    assert all(item.languages == ("it",) for item in pages)
    assert all(item.name == "Manga Reader" for item in pages)
    leads = [item for item in results if item.repository == "site-outbound"]
    assert len(leads) == 1
    assert leads[0].hostname == "linked.org"
    assert leads[0].family == "web-lead"


def test_scraping_respects_robots_and_refuses_private_targets(monkeypatch: pytest.MonkeyPatch):
    urls: list[str] = []

    def fake_fetch(_session: requests.Session, url: str, **_kwargs: object) -> bytes:
        urls.append(url)
        return b"User-agent: *\nDisallow: /\n"

    def public_host(_host: str) -> bool:
        return True

    def private_host(_host: str) -> bool:
        return False

    monkeypatch.setattr(discovery, "_fetch", fake_fetch)
    monkeypatch.setattr(discovery, "_public_dns_host", public_host)
    with requests.Session() as session:
        assert discovery.scrape_site(session, "private.org", set()) == []
    assert urls == ["https://private.org/robots.txt"]
    assert not discovery.safe_hostname("127.0.0.1")
    monkeypatch.setattr(discovery, "_public_dns_host", private_host)
    urls.clear()
    with requests.Session() as session:
        assert discovery.scrape_site(session, "private.org", set()) == []
    assert urls == []


def test_scraped_website_is_not_an_independent_catalog_vote():
    records = [
        discovery.Evidence("manga-site.org", "Site", "manga", (), "aidoku",
                           "reader", "aidoku", "baseURL"),
        discovery.Evidence("manga-site.org", "Manga Site", "manga", (),
                           "site-homepage", None, "web-lead", "html-title-description"),
    ]
    ledger, eligible = discovery.classify(records, {"manga-site.org"}, set(), set(), set())
    assert eligible == set()
    assert ledger[0]["independent_families"] == 1
    assert ledger[0]["status"] == "review"


def test_pr_proposal_filters_and_is_idempotent(tmp_path: Path):
    ledger = tmp_path / "candidates.jsonl"
    def record(host: str, families: int, status: str, joined: bool = False,
               scraped: bool = False) -> dict[str, object]:
        return {
            "hostname": host,
            "status": status,
            "independent_families": families,
            "evidence": ["site-homepage:html-title-description"] if scraped else ["aidoku:baseURL"],
            "joined": joined,
        }

    rows = [
        record("confirmed.org", 2, "validated-candidate"),
        record("scraped.org", 1, "review", joined=True, scraped=True),
        {**record("sitemap-reader.org", 1, "review", joined=True),
         "evidence": ["site-sitemap:sitemap-paths"], "categories": ["manga"], "languages": ["ja"]},
        record("one-catalog.org", 1, "review", joined=True),
        record("outbound-only.org", 0, "review", joined=True, scraped=True),
        record("already.org", 2, "validated-candidate"),
        record("pro.org", 2, "pro"),
        record("github.com", 2, "excluded"),
        record("blocked.org", 2, "validated-candidate"),
    ]
    ledger.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    approved = tmp_path / "approved.txt"
    approved.write_text("# Reviewed hosts\nalready.org\n", encoding="utf-8")
    pro = tmp_path / "pro.txt"
    pro.write_text("127.0.0.1 pro.org\n", encoding="utf-8")
    whitelist = tmp_path / "whitelist.txt"
    whitelist.write_text("blocked.org\n", encoding="utf-8")
    exceptions = tmp_path / "exceptions.txt"
    body = tmp_path / "pr-body.md"

    new = propose_sites(ledger, approved, pro, whitelist, exceptions, body)
    assert new == ["confirmed.org", "scraped.org", "sitemap-reader.org"]
    assert list(utils.iter_domains(approved)) == [
        "already.org", "confirmed.org", "scraped.org", "sitemap-reader.org",
    ]
    assert "confirmed.org" in body.read_text(encoding="utf-8")
    assert "scraped.org" in body.read_text(encoding="utf-8")
    assert "sitemap-reader.org" in body.read_text(encoding="utf-8")
    generated_body = body.read_text(encoding="utf-8")
    assert "ja" in generated_body
    assert "## New anime and manga sites for review" in generated_body
    assert "**3 new domains proposed**" in generated_body
    assert "| Domain | Sources | Categories | Languages | Independent catalogs |" in generated_body
    assert "| yes |" in generated_body
    assert "Before merging:" in generated_body
    assert "Nuovi siti" not in generated_body
    assert "sì" not in generated_body

    original = approved.read_bytes()
    assert propose_sites(ledger, approved, pro, whitelist, exceptions, body) == []
    assert approved.read_bytes() == original


def test_unmerged_discovery_report_cannot_enter_release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(utils, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path / "downloads"))
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "hosts"))
    monkeypatch.setenv("EXCEPTIONS_DIR", str(tmp_path / "exceptions"))
    monkeypatch.setenv("WHITELIST_FILE", str(tmp_path / "whitelist.txt"))
    download = tmp_path / "downloads" / "anime-manga"
    download.mkdir(parents=True)
    (download / "refined.txt").write_text("source.org\n", encoding="utf-8")
    (download / "newedgex-joined.txt").write_text("unapproved.org\n", encoding="utf-8")
    (download / source_catalog.DOWNLOAD_MANIFEST).write_text(
        "refined.txt\nnewedgex-joined.txt\n", encoding="utf-8",
    )
    report = tmp_path / "downloads" / "anime-discovery"
    report.mkdir()
    (report / "eligible-anime.txt").write_text("unapproved.org\n", encoding="utf-8")
    seeds = tmp_path / "seeds"
    seeds.mkdir()
    seed = seeds / "anime-manga.txt"
    seed.write_text("pro.org\n", encoding="utf-8")
    assert merge_hosts.main(["--category", "anime-manga", "--no-progress"]) == 0
    hosts = tmp_path / "hosts" / "anime-manga.txt"
    assert set(utils.iter_domains(hosts)) == {"source.org", "pro.org"}

    seed.write_text("pro.org\napproved.org\n", encoding="utf-8")
    assert merge_hosts.main(["--category", "anime-manga", "--no-progress"]) == 0
    assert set(utils.iter_domains(hosts)) == {"source.org", "pro.org", "approved.org"}
