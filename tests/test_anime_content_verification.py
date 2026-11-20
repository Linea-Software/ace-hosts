"""Offline, multilingual content verification and PR16 false-positive regressions."""
# pyright: reportImplicitStringConcatenation=false
from __future__ import annotations

from dataclasses import FrozenInstanceError
from dataclasses import replace

import pytest

from ace_anime_discovery.anime_content_verification import (
    ContentVerificationResult,
    verify_content_page,
)


def _page(title: str, body: str, *, head: str = "") -> str:
    return f"<html><head><title>{title}</title>{head}</head><body>{body}</body></html>"


def _chapter_links(*labels: str) -> str:
    return "".join(
        f'<a href="/work/42/chapter-{i}">{label}</a>'
        for i, label in enumerate(labels, 1)
    )


_LONG_TEXT = (
    "The lantern trembled over the mountain road as the travelers reached "
    "the deserted gate, where they found the old map folded beneath a stone. "
    "Nothing moved beyond the distant river, and neither of them spoke. "
)


@pytest.mark.parametrize(("title", "url", "body", "categories", "code"), [
    (
        "Read Manga: Starbound Chapter 12",
        "https://reader.example.org/series/starbound/chapter-12",
        '<div class="chapter-reader"><img src="/pages/01.jpg">'
        '<img data-src="/pages/02.jpg"></div>',
        (),
        "verified_reader",
    ),
    (
        "星の漫画 第12話",
        "https://reader.example.org/%E6%98%9F%E3%81%AE%E4%BD%9C%E5%93%81/12",
        '<section class="manga-reader"><img src="/a.jpg"><img src="/b.jpg"></section>',
        (),
        "verified_reader",
    ),
    (
        "한국 웹툰 제4화",
        "https://reader.example.org/works/fourth",
        '<div id="reading-area" class="webtoon-reader">'
        '<img data-original="/img/a.webp"><img src="/img/b.webp"></div>',
        (),
        "verified_reader",
    ),
    (
        "漫畫 星球 第17章",
        "https://reader.example.org/detail/42",
        '<div class="chapter-content"><img src="/image/001.jpg"></div>'
        '<a href="/detail/42/chapter-18">下一章</a>',
        (),
        "verified_reader",
    ),
    (
        "Watch Anime: Crystal Episode 8",
        "https://stream.example.org/watch/crystal/8",
        '<video controls><source src="/media/8.mp4" type="video/mp4"></video>',
        (),
        "verified_episode",
    ),
    (
        "أنمي الحلقة 7",
        "https://stream.example.org/عرض/abc",
        '<iframe class="episode-player" src="https://cdn.example.net/embed/7" allowfullscreen></iframe>'
        '<script type="application/ld+json">'
        '{"@context":"https://schema.org","@type":"TVEpisode","episodeNumber":7}'
        "</script>",
        (),
        "verified_episode",
    ),
    (
        "Light Novel: The Last Library Chapter 2",
        "https://novels.example.org/story/library/2",
        '<article class="novel-content"><p>' + _LONG_TEXT +
        '</p><p>' + _LONG_TEXT + "</p></article>",
        (),
        "verified_novel_chapter",
    ),
    (
        "小説 第十二章",
        "https://novels.example.org/作品/十二",
        '<div class="chapter-text"><p>' + _LONG_TEXT +
        '</p><p>' + _LONG_TEXT + "</p></div>",
        (),
        "verified_novel_chapter",
    ),
    (
        "Capitolo 6: Il castello",
        "https://novels.example.org/leggi/castello/6",
        '<div class="novel-body"><p>' + _LONG_TEXT +
        '</p><p>' + _LONG_TEXT + "</p></div>",
        ("webnovel",),
        "verified_novel_chapter",
    ),
    (
        "Manga Northwind — Series",
        "https://reader.example.org/series/northwind",
        '<h2>Chapters</h2>' + _chapter_links("Chapter 1", "Chapter 2", "Chapter 3"),
        (),
        "verified_series_chapters",
    ),
    (
        "星之漫畫 — 目录",
        "https://reader.example.org/work/alpha",
        '<h2>章节</h2>' + _chapter_links("第一章", "第二章"),
        (),
        "verified_series_chapters",
    ),
    (
        "The Traveler: Manga",
        "https://reader.example.org/work/traveler",
        '<script type="application/ld+json">'
        '{"@context":"https://schema.org","@type":"ComicSeries","name":"The Traveler"}'
        "</script><h2>Chapters</h2>" + _chapter_links("Chapter 1"),
        (),
        "verified_series_chapters",
    ),
    (
        "Novel Reading Hub — Wind Chapter 3",
        "https://novels.example.org/entry/abc/3",
        '<article><p>' + _LONG_TEXT + '</p><p>' + _LONG_TEXT + '</p></article>',
        (),
        "verified_novel_chapter",
    ),
])
def test_verified_real_content_structures(
    title: str,
    url: str,
    body: str,
    categories: tuple[str, ...],
    code: str,
) -> None:
    result = verify_content_page(_page(title, body), url, homepage_categories=categories)
    assert result.verified
    assert result.codes == (code,)
    assert result.categories
    assert result.positive_signals


@pytest.mark.parametrize(("title", "url", "body", "head"), [
    (
        "Anime Action Figures — Crystal Hero",
        "https://merchant.example.org/products/anime-figure-123",
        '<h1>Anime Action Figure</h1><p>Add to cart. In stock.</p>'
        '<img class="page-image" src="/product1.jpg">'
        '<img class="page-image" src="/product2.jpg">',
        '<script type="application/ld+json">'
        '{"@type":"Product","name":"Anime statue","offers":{"@type":"Offer","price":"14.99"}}'
        "</script>",
    ),
    (
        "Latest Anime Episode News",
        "https://times.example.org/news/anime-episode-streaming",
        '<video controls src="/news-clip.mp4"></video>'
        '<a href="/news/anime/episode-7">Episode 7 news</a>',
        '<script type="application/ld+json">{"@type":"NewsArticle"}</script>',
    ),
    (
        "Manga Interviews and Reviews",
        "https://reviews.example.org/blog/new-manga-chapters",
        '<div class="chapter-reader"><img src="/page1.png">'
        '<img src="/page2.png"></div>',
        "",
    ),
    (
        "Search Manga Chapters",
        "https://reader.example.org/search?q=blue",
        _chapter_links("Chapter 1", "Chapter 2"),
        "",
    ),
    (
        "Anime category",
        "https://reader.example.org/category/anime",
        _chapter_links("Chapter 1", "Chapter 2"),
        "",
    ),
    (
        "Webtoon directory",
        "https://reader.example.org/tag/webtoon",
        _chapter_links("Chapter 1", "Chapter 2"),
        "",
    ),
    (
        "Anime catalogue homepage",
        "https://reader.example.org/",
        '<video controls src="/ep1.mp4"></video><h2>Episode 1</h2>',
        "",
    ),
    (
        "Manga Starbound Chapter 12",
        "https://reader.example.org/series/starbound/chapter-12",
        '<img src="/cover.jpg"><p>New chapter is coming next month!</p>',
        "",
    ),
    (
        "Manga Starbound Chapter 12",
        "https://reader.example.org/series/starbound/chapter-12",
        '<img src="/cover.jpg"><img src="/header.jpg"><p>News about manga</p>',
        "",
    ),
    (
        "Dental professionals and manga",
        "https://dentist.example.org/services/consultation",
        '<h1>Dentist appointment</h1><p>We publish manga news.</p>',
        '<meta name="keywords" content="anime,manga,watch,reader">',
    ),
    (
        "Read manga and watch anime",
        "https://news.example.org/guides/how-to-read",
        '<script type="application/ld+json">{"@type":"VideoObject","name":"Anime"}</script>',
        "",
    ),
    (
        "Manga Series: Northwind",
        "https://reader.example.org/series/northwind",
        '<a href="https://other.example.org/chapter-1">Chapter 1</a>'
        '<a href="https://other.example.org/chapter-2">Chapter 2</a>',
        "",
    ),
    (
        "Novel Chapter 1",
        "https://novel.example.org/chapter-1",
        '<p>Short first paragraph.</p><p>Short second paragraph.</p>',
        "",
    ),
    (
        "403 Access Denied Manga Chapter",
        "https://reader.example.org/manga/abc",
        '<div class="chapter-reader"><img src="/1.jpg"><img src="/2.jpg"></div>',
        "",
    ),
])
def test_non_content_is_not_verified(
    title: str, url: str, body: str, head: str,
) -> None:
    result = verify_content_page(_page(title, body, head=head), url)
    assert not result.verified
    assert result.codes
    assert result.negative_signals


def test_news_and_commerce_are_hard_vetoes_even_with_content_markup() -> None:
    body = (
        '<section class="chapter-reader"><img src="/page1.jpg">'
        '<img src="/page2.jpg"></section><video controls src="/episode.mp4"></video>'
        + _chapter_links("Chapter 1", "Chapter 2")
    )
    for schema_type in ("Product", "NewsArticle", "BlogPosting"):
        html = _page(
            "Anime Manga Series Chapter 2",
            body,
            head='<script type="application/ld+json">'
            f'{{"@context":"https://schema.org","@type":"{schema_type}"}}'
            "</script>",
        )
        outcome = verify_content_page(html, "https://unknown.example.org/story/2")
        assert not outcome.verified
        assert ("commerce_page" in outcome.negative_signals or
                "editorial_page" in outcome.negative_signals)


def test_no_domain_hardcodes_and_obscure_path_with_site_context() -> None:
    for host in ("unlisted.example.org", "somewhere.invalid", "odd-reader.example.co"):
        body = '<div class="chapter-reader"><img src="/a.png"><img src="/b.png"></div>'
        result = verify_content_page(
            _page("第12章 星の物語", body),
            f"https://{host}/x/y/123?volume=2",
            homepage_categories=("manga",),
        )
        assert result.verified
        assert result.categories == ("manga",)


def test_categories_from_homepage_are_context_only_not_proof() -> None:
    result = verify_content_page(
        _page("A story about flowers", '<h1>Welcome to our website</h1>'),
        "https://unknown.example.org/friendly/story",
        homepage_categories=("manga", "other", "anime"),
    )
    assert not result.verified
    assert result.categories == ("anime", "manga")
    assert "no_content_structure" in result.negative_signals


def test_json_ld_keywords_alone_are_not_evidence() -> None:
    html = _page(
        "Dental Clinic",
        "<p>Read manga in the waiting room.</p>",
        head='<script type="application/ld+json">'
        '{"@type":"WebPage","keywords":"anime manga webtoon","name":"Manga Reader"}'
        "</script>",
    )
    result = verify_content_page(html, "https://clinic.example.org/consultations")
    assert not result.verified
    assert "no_supported_genre" in result.negative_signals


def test_invalid_json_ld_fails_closed_without_crashing() -> None:
    html = _page(
        "Manga Chapter 2",
        '<div class="chapter-reader"><img src="/1.png"><img src="/2.png"></div>',
        head='<script type="application/ld+json">{broken</script>',
    )
    outcome = verify_content_page(html, "https://reader.example.org/abc/2")
    assert outcome.verified
    assert "content_schema" not in outcome.positive_signals


def test_video_in_article_without_episode_semantics_is_not_content() -> None:
    result = verify_content_page(
        _page("Anime reviewer", '<iframe src="https://videos.example.net/embed/123" allowfullscreen></iframe>'),
        "https://site.example.org/2026/11/anime-reviewer",
    )
    assert not result.verified


def test_same_host_links_and_multiple_languages() -> None:
    result = verify_content_page(
        _page(
            "Leia Manga: Aventureiros - Série",
            '<h2>Capítulos</h2><a href="/obra/a/capitulo-1">Capítulo 1</a>'
            '<a href="/obra/a/capitulo-2">Capítulo 2</a>',
        ),
        "https://unknown.example.org/obra/a",
    )
    assert result.verified
    assert result.codes == ("verified_series_chapters",)


def test_result_is_typed_immutable_and_diagnostic_codes_do_not_leak_text() -> None:
    secret = "private-story-title"
    result = verify_content_page(
        _page(f"Manga {secret} Chapter 7",
              '<div class="chapter-reader"><img src="/a.png"><img src="/b.png"></div>'),
        f"https://reader.example.org/{secret}/chapter-7?token=sensitive",
    )
    assert isinstance(result, ContentVerificationResult)
    assert result.title.startswith("Manga")
    assert all(secret not in code and "sensitive" not in code for code in (
        *result.codes, *result.positive_signals, *result.negative_signals,
    ))
    with pytest.raises(FrozenInstanceError):
        setattr(result, "verified", False)
    assert replace(result, verified=False).verified is False


def test_malformed_url_fails_closed() -> None:
    result = verify_content_page("<title>Manga Chapter 1</title>", "not a url")
    assert result.codes == ("invalid_url",)
    assert not result.verified



def test_relative_video_embed_with_episode_context_is_verified() -> None:
    result = verify_content_page(
        _page("Anime Episode 12", '<iframe src="/embed/12" allowfullscreen></iframe>'),
        "https://unknown.example.org/a/opaque-id",
    )
    assert result.verified
    assert result.codes == ("verified_episode",)


@pytest.mark.parametrize("url", [
    "https://reader.example.org/anime/news/story",
    "https://reader.example.org/manga/tag/romance",
    "https://reader.example.org/series/alpha/search?q=chapter",
    "https://reader.example.org/series/alpha/shop/figures",
])
def test_nested_non_content_routes_are_rejected(url: str) -> None:
    outcome = verify_content_page(
        _page("Manga Chapter 2", '<div class="chapter-reader">'
              '<img src="/a.jpg"><img src="/b.jpg"></div>'),
        url,
    )
    assert not outcome.verified


def test_prominent_commerce_action_vetoes_opaque_merchandise_route() -> None:
    result = verify_content_page(
        _page(
            "Manga Chapter 7 - Action Figure",
            '<h1>Add to cart</h1><div class="chapter-reader">'
            '<img src="/figure1.jpg"><img src="/figure2.jpg"></div>',
        ),
        "https://unfamiliar.example.org/items/987",
    )
    assert not result.verified
    assert "commercial_action" in result.negative_signals


def test_news_headline_rejected_without_schema_or_news_url() -> None:
    result = verify_content_page(
        _page("Anime Episode 17 Review",
              '<video controls src="/episode-excerpt.mp4"></video>'),
        "https://unfamiliar.example.org/posts-2026/123",
    )
    assert not result.verified
    assert "editorial_page" in result.negative_signals



def test_oversized_html_remains_bounded_and_deterministic() -> None:
    html = _page(
        "Manga Chapter 1",
        '<section class="chapter-reader"><img src="/1.png">'
        '<img src="/2.png"></section>' + "padding" * 100_000,
    )
    first = verify_content_page(html, "https://reader.example.org/x/1")
    assert first == verify_content_page(html, "https://reader.example.org/x/1")
    assert first.verified
