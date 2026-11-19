"""High-precision regressions for SEO/link-farm anime discovery."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ace_anime_discovery.anime_crawler import ranked_links
from ace_anime_discovery.anime_discovery import (
    Evidence, SitePage, classify, site_categories,
)
from ace_anime_discovery.propose_anime_sites import propose_sites


@pytest.mark.parametrize("text", [
    "Visit Bucaramanga", "1touchmangaluru.com",
    "Professional animation services", "manganese alloy",
    "Mangaluru travel guide", "Watch this animated tutorial",
    "Company manhuafoo", "1daydental.com",
])
def test_unrelated_words_are_not_genre_evidence(text: str) -> None:
    assert site_categories(text) == ()


@pytest.mark.parametrize(("text", "expected"), [
    ("Read manga and watch anime", {"anime", "manga"}),
    ("漫画 と アニメ", {"anime", "manga"}),
    ("한국 웹툰", {"webtoon"}),
    ("Latest web novels and light-novels", {"webnovel", "light-novel"}),
    ("Best scanlations", {"scanlation"}),
])
def test_exact_genre_terms_still_match(text: str, expected: set[str]) -> None:
    assert set(site_categories(text)) == expected


def test_keyword_stuffing_in_body_or_jsonld_is_not_verified() -> None:
    html = (
        "<html><head><title>General dental clinic</title>"
        '<meta name="keywords" content="anime manga webtoon">'
        '<script type="application/ld+json">{"name":"manga anime"}</script>'
        "</head><body>We write about anime and manga as a side topic.</body></html>"
    )
    page = SitePage()
    page.feed(html)
    assert site_categories(page.topic_text) == ()


def test_generic_external_links_do_not_spread_from_homepages() -> None:
    html = (
        "<html><title>Manga homepage</title>"
        '<a href="https://1daydental.example.org/">Dental provider</a>'
        '<a href="https://3pointsrealty.example.org/">Real estate</a>'
        '<a href="https://reader.example.org/">Read manga online</a>'
        "</html>"
    )
    links = ranked_links(html, "https://manga-directory.example.org/")
    assert links == ("https://reader.example.org/",)


def test_directory_generic_link_budget_is_bounded() -> None:
    html = "".join(
        f'<a href="https://non-topic-{i}.example.org/">External</a>'
        for i in range(100)
    )
    links = ranked_links(
        html, "https://directory.example.org/", allow_generic_external=True
    )
    assert len(links) == 5


def test_pr13_style_web_only_false_positives_cannot_enter_seeds(tmp_path: Path) -> None:
    # All 200 proposed PR #13 entries claimed to be "website-verified"
    # despite having zero independent catalog families.
    examples = (
        "1-800travel.com", "1daydental.com",
        "3pointsrealty.com", "360-painting.com", "0536sport.com",
    )
    evidence: list[Evidence] = []
    for host in examples:
        evidence.extend([
            Evidence(host, None, "manga", (), "web-link", None,
                     "web-lead", "href", origin="https://link-farm.example.org/"),
            Evidence(host, "Manga anime", "anime", ("zh",),
                     "site-homepage", None, "web-lead",
                     "html-title-description", origin=f"https://{host}/"),
        ])
    ledger, approved = classify(evidence, set(), set(), set(), set())
    assert approved == set()
    assert {row["hostname"] for row in ledger} == set(examples)
    assert all(row["independent_families"] == 0 for row in ledger)
    assert all(row["review_eligible"] is False for row in ledger)
    assert all(row["status"] == "review" for row in ledger)
    assert all(row["confidence"] == 35 for row in ledger)

    # Replaying a stale PR #13 ledger must also fail closed, even if it
    # still claims "website-verified" and "review_eligible": true.
    legacy = {
        "hostname": "00asterisk.com",
        "independent_families": 0,
        "status": "website-verified",
        "verification_method": "website-metadata",
        "review_eligible": True,
        "joined": False,
        "repositories": ["site-homepage", "web-link"],
        "evidence": ["site-homepage:html-title-description", "web-link:href"],
    }
    path = tmp_path / "candidates.jsonl"
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in [*ledger, legacy]),
        encoding="utf-8",
    )
    seed = tmp_path / "seeds.txt"
    seed.write_text("", encoding="utf-8")
    pro = tmp_path / "pro.txt"
    pro.write_text("", encoding="utf-8")
    assert propose_sites(
        path, seed, pro, tmp_path / "whitelist",
        tmp_path / "exceptions", tmp_path / "pr.md",
    ) == []
    assert seed.read_text(encoding="utf-8") == ""


def test_legacy_outbound_queue_is_migrated_once(tmp_path: Path) -> None:
    from ace_anime_discovery.anime_crawler import Frontier

    db_path = tmp_path / "state.sqlite"
    old = Frontier(db_path)
    try:
        assert old.add(
            "https://random.example.org/", depth=1,
            source="outbound:spam.example.org",
        )
        assert old.add(
            "https://catalog.example.org/", depth=0, source="catalog",
        )
        old.evidence(Evidence(
            "random.example.org", None, "manga", (), "web-link",
            None, "web-lead", "href",
        ))
        # Simulate a checkpoint from the earlier, broad-link crawler.
        old.conn.execute(
            "DELETE FROM run_state WHERE key='outbound_policy_version'"
        )
    finally:
        old.close()

    migrated = Frontier(db_path)
    try:
        assert "random.example.org" not in migrated.seen_hosts()
        assert "catalog.example.org" in migrated.seen_hosts()
        assert not any(
            record.repository == "web-link"
            for record in migrated.records()
        )
        # New topical outbound links must survive subsequent restarts.
        assert migrated.add(
            "https://reader.example.org/", depth=1,
            source="outbound:catalog.example.org",
        )
    finally:
        migrated.close()

    later = Frontier(db_path)
    try:
        assert "reader.example.org" in later.seen_hosts()
        assert "catalog.example.org" in later.seen_hosts()
    finally:
        later.close()
