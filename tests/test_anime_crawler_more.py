"""Further offline tests for ranked links, redirects, retries and robots."""
from __future__ import annotations

from pathlib import Path

from scripts import anime_crawler as crawler
from scripts import anime_discovery as discovery
from scripts.anime_search import search_queries


def test_relevance_ranked_links_over_navigation() -> None:
    html = (
        "<html><title>Webtoon directory</title>"
        + "".join(f'<a href="https://generic{i}.example.org/">Company</a>'
                  for i in range(120))
        + '<a href="https://newsite.example.org/">Official manga reader</a>'
        + '<a href="/webtoon/creators">Webtoon creators</a></html>'
    )
    links = crawler.ranked_links(html, "https://directory.example.org/", limit=2)
    assert links == ("https://newsite.example.org/",
                     "https://directory.example.org/webtoon/creators")


def test_robots_denial_and_redirect_refusal(tmp_path: Path) -> None:
    requests: list[str] = []

    def forbid(url: str, _budget: crawler.RequestBudget, **_kw: object) -> crawler.PageResponse:
        requests.append(url)
        return crawler.PageResponse(200, "text/plain", b"User-agent: *\nDisallow: /\n")

    c = crawler.WebCrawler(crawler.CrawlConfig(tmp_path / "robots.sqlite",
                           max_browser=0, delay=0), fetch=forbid)
    try:
        c.seed([], directories=("https://directory.example.org/",), with_public_indexes=False)
        stats = c.run()
    finally:
        c.close()
    assert requests == ["https://directory.example.org/robots.txt"]
    assert stats["urls_crawled"] == 0
    assert stats["urls_failed"] == 1

    requests.clear()

    def redirect(url: str, _budget: crawler.RequestBudget, **_kw: object) -> crawler.PageResponse:
        requests.append(url)
        if url.endswith("robots.txt"):
            return crawler.PageResponse(404, "text/plain", b"")
        return crawler.PageResponse(302, "text/html", b"")

    c = crawler.WebCrawler(crawler.CrawlConfig(tmp_path / "redirect.sqlite",
                           max_browser=0, delay=0), fetch=redirect)
    try:
        c.seed([], directories=("https://directory.example.org/",), with_public_indexes=False)
        stats = c.run()
    finally:
        c.close()
    assert stats["urls_failed"] == 1
    assert len(requests) == 2


def test_transient_retry_and_sitemap_filter(tmp_path: Path) -> None:
    requests: list[str] = []

    def fetch(url: str, _budget: crawler.RequestBudget, **_kw: object) -> crawler.PageResponse:
        requests.append(url)
        if url.endswith("robots.txt"):
            return crawler.PageResponse(404, "text/plain", b"")
        if url.endswith("sitemap.xml"):
            return crawler.PageResponse(200, "application/xml", (
                b"<urlset><url><loc>https://manga-home.example.org/manga/a</loc></url>"
                b"<url><loc>https://manga-home.example.org/manga/b</loc></url>"
                b"<url><loc>https://internal.example.org/manga/excluded</loc></url></urlset>"
            ))
        if requests.count("https://manga-home.example.org/") == 1 and url.endswith("manga-home.example.org/"):
            return crawler.PageResponse(503, "text/html", b"Unavailable")
        return crawler.PageResponse(200, "text/html", b"<title>Manga reader</title>")

    c = crawler.WebCrawler(crawler.CrawlConfig(tmp_path / "retry.sqlite",
                           max_browser=0, delay=0, max_pages_domain=2, max_depth=2), fetch=fetch)
    try:
        c.seed([discovery.Evidence("manga-home.example.org", "Reader", "manga",
                                  ("en",), "aidoku", None, "aidoku", "baseURL")],
               with_public_indexes=False)
        stats = c.run()
    finally:
        c.close()
    assert stats["retry_count"] == 1
    assert stats["urls_discovered"] == 3
    assert not any("internal.example.org" in url for url in requests)


def test_custom_template() -> None:
    values = list(search_queries(languages=("it",), categories=("manhwa",),
                                 max_queries=1, templates={"it": "{topic} siti {terms}"}))
    assert values == [("it", "manhwa siti leggere guardare online")]
