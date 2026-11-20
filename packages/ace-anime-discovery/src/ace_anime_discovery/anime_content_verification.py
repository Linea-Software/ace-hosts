"""Deterministic, conservative verification of public anime and reading pages.

This module accepts already-fetched HTML; it performs no I/O, runs no script,
and exposes only bounded title/category metadata and stable diagnostic codes.
In particular, a topical title or a plausible URL is *not* content evidence.
"""
# pyright: reportImplicitStringConcatenation=false
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import cast
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

MAX_HTML_CHARS = 650_000
MAX_TITLE_CHARS = 200
MAX_JSON_LD_CHARS = 32_000
MAX_LINKS = 250
MAX_IMAGES = 200

_VISUAL = frozenset({"manga", "manhwa", "manhua", "webtoon", "scanlation"})
_NOVELS = frozenset({"light-novel", "webnovel"})
_SUPPORTED = _VISUAL | _NOVELS | {"anime"}
_CATEGORY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("anime", re.compile(r"(?<![a-z])animes?(?![a-z])|アニメ|动漫|動畫|애니메이션|аниме|أنمي|انمي", re.I)),
    ("manga", re.compile(r"(?<![a-z])mangas?(?![a-z])|漫画|漫畫|マンガ|マンガ|مانجا|مانغا|манга|มังงะ|truyện tranh|fumetti giapponesi", re.I)),
    ("manhwa", re.compile(r"(?<![a-z])manhwa(?![a-z])|만화|манхва", re.I)),
    ("manhua", re.compile(r"(?<![a-z])manhua(?![a-z])|国漫|國漫", re.I)),
    ("webtoon", re.compile(r"(?<![a-z])webtoons?(?![a-z])|웹툰|条漫|條漫", re.I)),
    ("scanlation", re.compile(r"(?<![a-z])scanlations?(?![a-z])|scantrad|scanlationes", re.I)),
    ("light-novel", re.compile(r"(?<![a-z])light[\s_-]*novels?(?![a-z])|ライトノベル|ラノベ|라이트노벨|ранобэ", re.I)),
    ("webnovel", re.compile(r"(?<![a-z])web[\s_-]*novels?(?![a-z])|网络小说|網絡小說|웹소설|tiểu thuyết mạng", re.I)),
)
_CHAPTER = re.compile(
    r"(?<![a-z])(?:chapters?|episodes?|eps?\.?\s*\d+|cap[ií]tulos?|"
    r"epis[oó]dios?|chapitres?|kapitel|capitoli?|episodi|"
    r"b[oö]l[uü]m|g[lł]ava|глава|серия|第\s*\d+\s*[話话章回]|"
    r"第.{1,12}(?:話|话|章|回)|\d+\s*화|ตอนที่\s*\d+|"
    r"章节|章節|ตอน\s*\d+)(?![a-z])",
    re.I,
)
_SERIES = re.compile(
    r"(?<![a-z])(?:series|serie|série|season|seasons|"
    r"chapters?|cap[ií]tulos?|chapitres?|目录|目錄|목록|"
    r"作品|連載|연재|章节|章節)(?![a-z])", re.I,
)
_NOVEL_CHAPTER = re.compile(
    r"(?<![a-z])(?:novels?|romanzo|romance|小説|小说|小說|소설|"
    r"นิยาย|truy[eệ]n|глава)(?![a-z])", re.I,
)
_READER = re.compile(
    r"(?<![a-z])(?:manga[-_\s]?reader|comic[-_\s]?reader|"
    r"chapter[-_\s]?(?:reader|images|pages|content)|"
    r"reader[-_\s]?(?:images|pages|container)|reading[-_\s]?area|"
    r"webtoon[-_\s]?reader|novel[-_\s]?(?:body|content)|"
    r"page[-_\s]?(?:image|img)|reading-content)(?![a-z])",
    re.I,
)
_NOVEL_BODY = re.compile(
    r"(?:novel|chapter|reading|story)[-_\s]?(?:body|content|text)|"
    r"(?:entry-content|reading-area)", re.I,
)
_PAGE_IMAGE = re.compile(r"(?:page|chapter|reader|comic|manga)[-_ ]?\d{1,4}\b|(?:page|chapter)[-_ ]?(?:image|img)", re.I)

_NON_CONTENT_SEGMENTS = frozenset({
    "search", "searches", "category", "categories", "tag", "tags",
    "genre", "genres", "directory", "browse", "archives", "archive",
    "collections", "filter", "filters",
})
_NEWS_SEGMENTS = frozenset({
    "news", "blog", "blogs", "article", "articles", "posts", "post",
    "reviews", "review", "interviews", "interview", "press",
})
_SHOP_SEGMENTS = frozenset({
    "shop", "store", "product", "products", "merch", "merchandise",
    "cart", "checkout", "collections-shop",
})
_ERROR_TITLE = re.compile(
    r"^(?:404|403|not found|access denied|just a moment|error|"
    r"under construction|coming soon)\b", re.I,
)
_EDITORIAL_TITLE = re.compile(
    r"(?<![a-z])(?:news|reviews?|recap|interviews?|press release|"
    r"release date|announced|breaking)(?![a-z])", re.I,
)
_PRODUCT_ACTION = re.compile(
    r"(?<![a-z])(?:add to cart|buy now|shop now|in stock|"
    r"add to basket|ajouter au panier|comprar ahora|"
    r"aggiungi al carrello|カートに入れる|장바구니)(?![a-z])", re.I,
)


@dataclass(frozen=True)
class ContentVerificationResult:
    """Bounded, reproducible verification decision and diagnostic signal codes."""

    verified: bool
    codes: tuple[str, ...]
    title: str
    categories: tuple[str, ...]
    positive_signals: tuple[str, ...]
    negative_signals: tuple[str, ...]


def _clean(text: str, limit: int = MAX_TITLE_CHARS) -> str:
    return " ".join(text.split())[:limit]


def _categories(text: str) -> set[str]:
    return {name for name, pattern in _CATEGORY_PATTERNS if pattern.search(text)}


def _schema_types(value: object) -> set[str]:
    """Read only JSON-LD @type, never untrusted names or descriptions."""
    result: set[str] = set()
    stack: list[tuple[object, int]] = [(value, 0)]
    visited = 0
    while stack and visited < 160:
        current, depth = stack.pop()
        visited += 1
        if depth >= 7:
            continue
        if isinstance(current, dict):
            obj = cast(dict[str, object], current)
            raw_type = obj.get("@type")
            types: list[object] = (
                cast(list[object], raw_type) if isinstance(raw_type, list)
                else [raw_type]
            )
            for item in types:
                if isinstance(item, str):
                    result.add(item.rsplit("/", 1)[-1].casefold())
            for child in obj.values():
                if isinstance(child, (dict, list)):
                    stack.append((cast(object, child), depth + 1))
        elif isinstance(current, list):
            stack.extend((child, depth + 1) for child in cast(list[object], current)[:60])
    return result


class _ContentParser(HTMLParser):
    """Extract structural facts, not an unbounded copy of document text."""

    def __init__(self, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url: str = page_url
        self.host: str = urlsplit(page_url).hostname or ""
        self.title_parts: list[str] = []
        self.og_title: str = ""
        self.description: str = ""
        self.headings: list[str] = []
        self.body_text_samples: list[str] = []
        self.link_targets: set[str] = set()
        self.reader_images: int = 0
        self.reader_container: bool = False
        self.video_sources: int = 0
        self.iframes: int = 0
        self.novel_chars: int = 0
        self.novel_paragraphs: int = 0
        self.novel_container: bool = False
        self.schema_types: set[str] = set()
        self._in_title: bool = False
        self._heading_level: int = 0
        self._heading_parts: list[str] = []
        self._anchor_href: str = ""
        self._anchor_parts: list[str] = []
        self._paragraph_depth: int = 0
        self._paragraph_parts: list[str] = []
        self._json_ld: bool = False
        self._json_ld_parts: list[str] = []
        self._hidden: int = 0
        self._containers: list[tuple[str, bool, bool]] = []

    def _in_reader(self) -> bool:
        return any(reader for _, reader, _ in self._containers)

    def _in_novel(self) -> bool:
        return any(novel for _, _, novel in self._containers)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:  # pyright: ignore[reportImplicitOverride]
        a = {key.lower(): (value or "")[:1024] for key, value in attrs}
        classes = " ".join((a.get("class", ""), a.get("id", ""),
                            a.get("itemprop", "")))
        if tag in {"div", "section", "main", "article"}:
            reader = bool(_READER.search(classes))
            novel = bool(_NOVEL_BODY.search(classes))
            self._containers.append((tag, reader, novel))
            self.reader_container |= reader
            self.novel_container |= novel
        if tag == "title":
            self._in_title = True
        elif tag in {"h1", "h2", "h3"}:
            self._heading_level += 1
            self._heading_parts = []
        elif tag == "a":
            self._anchor_href = a.get("href", "")
            self._anchor_parts = []
        elif tag == "p":
            self._paragraph_depth += 1
            self._paragraph_parts = []
        elif tag == "script":
            self._json_ld = a.get("type", "").casefold() == "application/ld+json"
            self._json_ld_parts = []
            self._hidden += 1
        elif tag in {"style", "noscript", "template"}:
            self._hidden += 1
        elif tag == "meta":
            key = (a.get("property") or a.get("name") or "").casefold()
            if key in {"og:title", "twitter:title"} and not self.og_title:
                self.og_title = _clean(a.get("content", ""))
            if key in {"description", "og:description", "twitter:description"} and not self.description:
                self.description = _clean(a.get("content", ""), 300)
        elif tag == "img" and self.reader_images < MAX_IMAGES:
            source = (a.get("src") or a.get("data-src") or
                      a.get("data-original") or a.get("data-lazy-src"))
            if source and not source.startswith(("data:", "javascript:")) and (
                self._in_reader() or _READER.search(classes)
                or _PAGE_IMAGE.search(" ".join((classes, a.get("alt", ""), source.rsplit("/", 1)[-1])))
            ):
                self.reader_images += 1
        elif tag == "video":
            if (a.get("src") or "controls" in dict(attrs) or
                    a.get("data-src")):
                self.video_sources += 1
        elif tag == "source":
            if a.get("src") and a.get("type", "").casefold().startswith("video/"):
                self.video_sources += 1
        elif tag == "iframe":
            src = a.get("src", "")
            if src and not src.startswith(("javascript:", "data:", "#")) and (
                "allowfullscreen" in dict(attrs)
                or "player" in classes.casefold()
                or "embed" in src.casefold()
            ):
                self.iframes += 1

    def handle_data(self, data: str) -> None:  # pyright: ignore[reportImplicitOverride]
        if self._json_ld:
            if sum(map(len, self._json_ld_parts)) < MAX_JSON_LD_CHARS:
                self._json_ld_parts.append(data[:MAX_JSON_LD_CHARS])
            return
        if self._hidden:
            return
        if self._in_title and len(self.title_parts) < 8:
            self.title_parts.append(data[:200])
        if self._heading_level and len(self._heading_parts) < 15:
            self._heading_parts.append(data[:200])
        if self._anchor_href and len(self._anchor_parts) < 8:
            self._anchor_parts.append(data[:140])
        if self._paragraph_depth and len(self._paragraph_parts) < 100:
            self._paragraph_parts.append(data[:3000])
        if len(self.body_text_samples) < 30 and data.strip():
            self.body_text_samples.append(data[:150])

    def handle_endtag(self, tag: str) -> None:  # pyright: ignore[reportImplicitOverride]
        if tag == "title":
            self._in_title = False
        elif tag in {"h1", "h2", "h3"} and self._heading_level:
            heading = _clean(" ".join(self._heading_parts), 220)
            if heading and len(self.headings) < 12:
                self.headings.append(heading)
            self._heading_level -= 1
            self._heading_parts = []
        elif tag == "a" and self._anchor_href:
            self._record_link(self._anchor_href, " ".join(self._anchor_parts))
            self._anchor_href = ""
            self._anchor_parts = []
        elif tag == "p" and self._paragraph_depth:
            paragraph = _clean(" ".join(self._paragraph_parts), 6000)
            if len(paragraph) >= 50:
                self.novel_paragraphs += 1
                self.novel_chars += len(paragraph)
            self._paragraph_depth -= 1
            self._paragraph_parts = []
        elif tag == "script":
            if self._json_ld and self._json_ld_parts:
                try:
                    payload = cast(object, json.loads(
                        "".join(self._json_ld_parts)[:MAX_JSON_LD_CHARS]
                    ))
                    self.schema_types.update(_schema_types(payload))
                except (ValueError, TypeError, RecursionError):
                    pass
            self._json_ld = False
            self._json_ld_parts = []
            self._hidden = max(0, self._hidden - 1)
        elif tag in {"style", "noscript", "template"}:
            self._hidden = max(0, self._hidden - 1)
        elif tag in {"div", "section", "main", "article"}:
            for index in range(len(self._containers) - 1, -1, -1):
                if self._containers[index][0] == tag:
                    del self._containers[index:]
                    break

    def _record_link(self, href: str, label: str) -> None:
        if len(self.link_targets) >= MAX_LINKS:
            return
        if href.startswith(("javascript:", "data:", "#", "mailto:")):
            return
        target = urlsplit(urljoin(self.page_url, href))
        if target.scheme not in {"https", "http"} or target.hostname != self.host:
            return
        this = urlsplit(self.page_url)
        path = unquote(target.path)
        # The anchor must name actual chapter/episode navigation, not a topical
        # URL alone. The label can establish this for opaque numeric routes.
        if not _CHAPTER.search(path + " " + label):
            return
        if (target.path == this.path and target.query == this.query):
            return
        self.link_targets.add(target.path + "?" + target.query)

    @property
    def title(self) -> str:
        return _clean(" ".join(self.title_parts) or self.og_title or
                      (self.headings[0] if self.headings else ""))


def verify_content_page(
    html: str,
    page_url: str,
    *,
    homepage_categories: tuple[str, ...] = (),
) -> ContentVerificationResult:
    """Require onsite content semantics, not topical metadata or URL shape.

    The caller supplies a permitted, successfully fetched public HTML page.
    This pure function does not fetch, execute JavaScript, retain HTML or
    expose excerpts in signal codes. Homepage genres may support verification,
    but cannot independently verify a content page.
    """
    parsed_url = urlsplit(page_url)
    if parsed_url.scheme not in {"https", "http"} or not parsed_url.hostname:
        return ContentVerificationResult(False, ("invalid_url",), "", (), (), ("invalid_url",))
    parser = _ContentParser(page_url)
    parser.feed(html[:MAX_HTML_CHARS])
    title = parser.title
    prominent = " ".join((title, parser.og_title, parser.description, *parser.headings))
    categories = _categories(prominent) | (set(homepage_categories) & _SUPPORTED)
    # Bare "novel" is useful only when an actual chapter is readable.
    if _NOVEL_CHAPTER.search(prominent) and not categories and parser.novel_chars >= 140:
        categories.add("webnovel")
    positives: set[str] = set()
    negatives: set[str] = set()
    types = parser.schema_types
    segments = [segment.casefold() for segment in unquote(parsed_url.path).split("/") if segment]
    first = segments[1:] if segments and re.fullmatch(r"[a-z]{2}(?:-[a-z]{2})?", segments[0]) else segments
    query = parse_qs(parsed_url.query.casefold(), keep_blank_values=True)

    if any(segment in _NON_CONTENT_SEGMENTS for segment in first) or {"q", "s", "search", "query"} & query.keys():
        negatives.add("listing_or_search_route")
    if any(segment in _SHOP_SEGMENTS for segment in first) or {"product", "offer"} & types:
        negatives.add("commerce_page")
    if any(segment in _NEWS_SEGMENTS for segment in first) or {"newsarticle", "blogposting"} & types:
        negatives.add("editorial_page")
    if not segments:
        negatives.add("homepage_route")
    if _EDITORIAL_TITLE.search(title):
        negatives.add("editorial_page")
    if not title or _ERROR_TITLE.search(title):
        negatives.add("missing_or_error_title")

    if parser.link_targets:
        positives.add("onsite_chapter_episode_links")
    if parser.reader_images:
        positives.add("reader_images")
    if parser.video_sources or parser.iframes:
        positives.add("playable_video")
    if parser.novel_chars >= 140 and parser.novel_paragraphs >= 2:
        positives.add("longform_paragraphs")
    if parser.novel_container:
        positives.add("novel_content_container")
    comic_schema = bool(types & {
        "comicstory", "comicseries", "comicissue", "visualartwork", "comic",
    })
    series_schema = bool(types & {"creativeworkseries", "bookseries", "comicseries"})
    episode_schema = bool(types & {"tvepisode", "episode"})
    if comic_schema or series_schema or episode_schema or types & {"chapter", "book"}:
        positives.add("content_schema")
    if _PRODUCT_ACTION.search(" ".join(parser.body_text_samples)):
        # Store an action signal, but require the combined structural evidence
        # of a shop route or product schema to treat it as a hard veto.
        negatives.add("commercial_action")
    if not categories:
        negatives.add("no_supported_genre")
    if not parser.link_targets and not parser.reader_images and not (
        parser.video_sources or parser.iframes or parser.novel_chars >= 140
    ):
        negatives.add("no_content_structure")

    heading_text = " ".join((title, *parser.headings))
    chapter_context = bool(_CHAPTER.search(heading_text + " " + unquote(parsed_url.path)))
    novel_context = bool(_NOVEL_CHAPTER.search(heading_text + " " + unquote(parsed_url.path)))
    series_context = bool(_SERIES.search(heading_text + " " + unquote(parsed_url.path)))
    count_links = len(parser.link_targets)
    content_code = ""
    if categories & _VISUAL and (
        (parser.reader_images >= 2 and
         (parser.reader_container or chapter_context or comic_schema or count_links > 0))
        or (parser.reader_images >= 1 and chapter_context and count_links >= 1)
    ):
        content_code = "verified_reader"
    elif "anime" in categories and (parser.video_sources or parser.iframes) and (
        chapter_context or episode_schema
    ):
        content_code = "verified_episode"
    elif categories & _NOVELS and parser.novel_paragraphs >= 2 and parser.novel_chars >= 140 and (
        chapter_context or novel_context or parser.novel_container or "chapter" in types
    ):
        content_code = "verified_novel_chapter"
    elif (categories & (_VISUAL | _NOVELS)) and (
        (count_links >= 2 and (series_context or comic_schema or series_schema))
        or (count_links >= 1 and series_schema and series_context)
    ):
        content_code = "verified_series_chapters"

    hard_veto = negatives & {
        "listing_or_search_route", "commerce_page", "editorial_page",
        "homepage_route", "missing_or_error_title", "no_supported_genre",
        "commercial_action",
    }
    if "commercial_action" in negatives and (
        "commerce_page" in negatives or "product" in types
    ):
        hard_veto.add("commerce_page")
    verified = bool(content_code) and not hard_veto
    if not content_code:
        negatives.add("insufficient_content_evidence")
    codes = (content_code,) if verified else tuple(sorted(hard_veto)) or ("insufficient_content_evidence",)
    return ContentVerificationResult(
        verified=verified,
        codes=codes,
        title=title,
        categories=tuple(sorted(categories)),
        positive_signals=tuple(sorted(positives)),
        negative_signals=tuple(sorted(negatives)),
    )
