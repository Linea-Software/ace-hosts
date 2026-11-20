"""Offline regression tests for tolerant HTML parsing and conservative evidence."""
# pyright: reportImplicitStringConcatenation=false
from __future__ import annotations

from bs4 import BeautifulSoup

from ace_anime_discovery.anime_content_verification import verify_content_page
from ace_anime_discovery.anime_crawler import ranked_links
from ace_anime_discovery.anime_discovery import DirectoryLinks, SitePage


def test_malformed_html_keeps_bounded_metadata_and_safe_visible_text() -> None:
    markup = (
        '<html lang="it"><head><title>Leggi manga</title>'
        '<meta name="description" content="Read manga online">'
        '<link rel="alternate canonical" href="https://reader.example.org/">'
        '</head><body><h1>Fumetti giapponesi<h2>Chapter releases'
        '<script>addToCart("buy now")</script>'
        '<script type="application/ld+json">{"@type":"WebPage"}</script>'
        '<p>Benvenuto nel catalogo</p>'
        '<a href="/chapter/4"><span>Chapter <b>4</b></span></a>'
    )
    page = SitePage()
    page.feed(markup)
    assert "manga" in page.topic_text.casefold()
    assert "addToCart" not in page.visible_text
    assert '"@type"' not in page.visible_text
    assert page.canonical == "https://reader.example.org/"
    assert page.language == "it"
    assert "/chapter/4" in page.links
    assert page.soup is not None


def test_directory_links_are_capped_and_exclude_javascript_text() -> None:
    page = DirectoryLinks()
    page.feed('<a href="/comic/1">Comic</a><a href="/comic/2"><b>Manga</b></a>')
    assert page.links == ["/comic/1", "/comic/2"]


def test_reused_tree_has_identical_link_ranking_and_verification() -> None:
    markup = (
        '<html><title>Read manga Star Chapter 4</title><body>'
        '<section class="manga-reader"><img src="/pages/1.png">'
        '<img src="/pages/2.png"></section>'
        '<a href="/manga/star/chapter-5"><b>Chapter 5</b></a>'
        '</body></html>'
    )
    url = "https://reader.example.org/manga/star/chapter-4"
    parsed = BeautifulSoup(markup, "lxml")
    assert ranked_links(markup, url) == ranked_links(markup, url, parsed_soup=parsed)
    assert verify_content_page(markup, url) == verify_content_page(
        markup, url, parsed_soup=parsed,
    )


def test_non_reader_commerce_and_editorial_remain_rejected() -> None:
    for url, title, body in [
        (
            "https://reader.example.org/products/manga-figures",
            "Manga Chapter 12 Figures",
            '<div class="manga-reader"><img src="/1.png"><img src="/2.png"></div>',
        ),
        (
            "https://reader.example.org/news/manga-chapter-12",
            "Anime news Chapter 12",
            '<a href="/news/chapter-12">Chapter 12</a>'
            '<a href="/news/chapter-13">Chapter 13</a>',
        ),
    ]:
        result = verify_content_page(
            f"<html><title>{title}</title><body>{body}</body></html>", url,
        )
        assert result.verified is False


def test_topical_homepage_can_sample_opaque_internal_content_routes() -> None:
    url = "https://reader.example.org/"
    markup = (
        '<a href="/work/281">Read manga Chapter 12</a>'
        '<a href="/book/918">Read webnovel Chapter 13</a>'
        '<a href="/privacy">Privacy policy</a>'
        '<a href="/contact">Contact support</a>'
    )
    assert ranked_links(markup, url) == ()
    links = ranked_links(markup, url, allow_opaque_internal=True)
    assert links == (
        "https://reader.example.org/book/918",
        "https://reader.example.org/work/281",
    )


def test_script_based_seo_spam_never_becomes_site_topic() -> None:
    page = SitePage()
    page.feed(
        '<html><title>General interest</title><body>'
        '<script type="application/ld+json">{"keywords":"manga anime webtoon"}</script>'
        '<p>Huge manga manhua anime webtoon directory!</p></body></html>'
    )
    assert page.topic_text.strip() == "General interest"


def test_irrelevant_navigation_before_chapters_does_not_hide_series() -> None:
    navigation = "".join(
        f'<a href="/navigation/{n}">About</a>' for n in range(260)
    )
    html = (
        "<title>Manga Reader — Series</title><h2>Chapters</h2>"
        + navigation
        + '<a href="/work/series/chapter-1">Chapter 1</a>'
        + '<a href="/work/series/chapter-2">Chapter 2</a>'
    )
    result = verify_content_page(html, "https://reader.example.org/work/series")
    assert result.verified
    assert result.codes == ("verified_series_chapters",)


def test_decorative_images_before_real_reader_are_ignored() -> None:
    navigation = "".join(
        f'<img src="/decoration/{n}.png" alt="Site icon">' for n in range(201)
    )
    html = (
        "<title>Manga Star Chapter 12</title>"
        + navigation
        + '<section class="chapter reader">'
        + '<img src="/pages/001.png"><img src="/pages/002.png">'
        + "</section>"
    )
    result = verify_content_page(
        html, "https://reader.example.org/work/star/chapter-12",
    )
    assert result.verified
    assert result.codes == ("verified_reader",)


def test_multitoken_novel_container_is_detected() -> None:
    paragraph = (
        "The travelers reached the river just before the storm and found "
        "a weathered book wrapped in cloth near an abandoned hut. "
    )
    html = (
        "<title>Light Novel Wind — Chapter 4</title>"
        '<article class="novel content"><p>' + paragraph + "</p><p>"
        + paragraph + "</p></article>"
    )
    result = verify_content_page(
        html, "https://novel.example.org/story/wind/4",
    )
    assert result.verified
    assert "novel_content_container" in result.positive_signals


def test_small_commercial_banner_does_not_reject_real_reader() -> None:
    html = (
        "<title>Manga Series Star Chapter 12</title>"
        "<p>Buy now: supporter merchandise available</p>"
        '<section class="manga-reader">'
        '<img src="/chapter/001.png"><img src="/chapter/002.png">'
        "</section>"
    )
    result = verify_content_page(
        html, "https://reader.example.org/chapter/star/12",
    )
    assert result.verified
    assert "commercial_action" in result.negative_signals
