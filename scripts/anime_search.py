"""Configurable multilingual search plans for anime and manga discovery.

Search results are unverified leads. This module neither scrapes SERPs nor
equates a search hit with eligibility for any published blocklist.
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import cast

LANGUAGE_TERMS: dict[str, str] = {
    "en": "read watch online", "ja": "無料 読む 視聴", "ko": "무료 보기 읽기",
    "zh-hans": "在线 阅读 免费", "zh-hant": "線上 閱讀 免費",
    "it": "leggere guardare online", "es": "leer ver en línea",
    "pt": "ler assistir online", "fr": "lire regarder en ligne",
    "de": "online lesen ansehen", "ru": "читать смотреть онлайн",
    "id": "baca nonton online", "vi": "đọc xem trực tuyến",
    "th": "อ่าน ดู ออนไลน์", "tr": "oku izle çevrimiçi",
    "ar": "قراءة مشاهدة اونلاين",
}
TOPICS: dict[str, dict[str, str]] = {
    "anime": {"ja": "アニメ", "ko": "애니메이션", "zh-hans": "动漫",
              "zh-hant": "動畫", "ar": "انمي"},
    "manga": {"ja": "漫画", "ko": "만화", "zh-hans": "漫画",
              "zh-hant": "漫畫", "ar": "مانجا"},
    "manhwa": {"ko": "만화", "ja": "韓国漫画"},
    "manhua": {"zh-hans": "国漫", "zh-hant": "國漫"},
    "webtoon": {"ko": "웹툰", "zh-hans": "条漫", "zh-hant": "條漫"},
    "scanlation": {"ja": "翻訳 漫画", "es": "scanlation manga"},
    "light-novel": {"ja": "ライトノベル", "ko": "라이트노벨"},
    "webnovel": {"zh-hans": "网络小说", "zh-hant": "網絡小說",
                 "ko": "웹소설", "vi": "tiểu thuyết mạng"},
}


def search_queries(*, languages: tuple[str, ...] | None = None,
                   categories: tuple[str, ...] | None = None,
                   max_queries: int = 24,
                   templates: dict[str, str] | None = None) -> Iterator[tuple[str, str]]:
    """Rotate language and category diagonally for diverse small-budget runs."""
    if not 0 <= max_queries <= 300:
        raise ValueError("invalid query budget")
    languages = tuple(LANGUAGE_TERMS) if languages is None else languages
    categories = tuple(TOPICS) if categories is None else categories
    if not languages or not categories:
        return
    if any(lang not in LANGUAGE_TERMS for lang in languages):
        raise ValueError("unsupported language")
    if any(category not in TOPICS for category in categories):
        raise ValueError("unsupported category")
    templates = templates or {}
    for i in range(max_queries):
        lang = languages[i % len(languages)]
        category = categories[(i + i // len(languages)) % len(categories)]
        topic = TOPICS[category].get(lang, category.replace("-", " "))
        pattern = templates.get(lang, "{topic} {terms} site directory")
        if len(pattern) > 220 or ("{topic}" not in pattern and "{terms}" not in pattern):
            raise ValueError("invalid query template")
        query = pattern.replace("{topic}", topic).replace("{terms}", LANGUAGE_TERMS[lang])
        yield lang, query[:250]


def parse_brave_results(payload: object) -> list[tuple[str, str]]:
    """Read the documented Brave JSON response; never trust result markup."""
    if not isinstance(payload, dict):
        raise ValueError("invalid search response")
    web = cast(dict[str, object], payload).get("web")
    results = cast(dict[str, object], web).get("results") if isinstance(web, dict) else None
    if not isinstance(results, list):
        raise ValueError("missing web.results")
    parsed: list[tuple[str, str]] = []
    for entry in cast(list[object], results[:30]):
        if not isinstance(entry, dict):
            continue
        result = cast(dict[str, object], entry)
        url = result.get("url")
        title = result.get("title")
        if isinstance(url, str) and url.startswith("https://"):
            parsed.append((url, title[:200] if isinstance(title, str) else ""))
    return parsed
