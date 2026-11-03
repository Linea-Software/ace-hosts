# Anime/Manga discovery and review

ACE Hosts exposes two stable, independent site-pack IDs:

- **Pro**: pack-anime-manga-pro → anime-manga.txt. Curated UT1 manga + newedgex refined + reviewed local seeds/anime-manga.txt. The default option; no weak discovery hit is promoted to Pro.
- **Extended**: pack-anime-manga-extended → anime-manga-extended.txt. Optional broader list containing Pro **plus only the hostnames approved by merging a discovery PR** into `seeds/anime-manga-extended.txt`. General-purpose services are excluded.

Extended is **not** a dump of the roughly 103,000 entries from newedgex joined. Its raw source is never directly merged into a public blocklist.

## Upstream catalogs and adapter formats

The independent parser functions in scripts/anime_discovery.py extract only declared site-home URLs, not arbitrary URLs found in extension source code, icons, authentication endpoints, APIs or CDNs.

| Upstream | Explicit input and field | License / caveat |
| --- | --- | --- |
| Keiyoushi/Mihon | keiyoushi/extensions repo index.min.json → sources[].baseUrl | Apache-2.0; presently only a small placeholder set |
| Aniyomi | aniyomiorg/aniyomi-extensions repo index.min.json → sources[].baseUrl | Apache-2.0; currently a small index |
| Mangayomi manga/anime | kodjodevf/mangayomi-extensions index.json → baseUrl | Apache-2.0; **archived 2025** |
| Mangayomi novels | Same repo novel_index.json → baseUrl | Apache-2.0; same independent family as manga |
| Aidoku Community | Aidoku-Community/sources index.min.json → sources[].baseURL | MIT OR Apache-2.0 |
| Miru | miru-project/repo index.json → webSite | MIT |
| LNReader | LNReader/lnreader-plugins plugins/v3.0.0/.dist/plugins.min.json → site | Verify upstream per-plugin license; replaces obsolete lnreader-sources |
| kuiti cross-ecosystem index | kuiti/manga-source-index 01-all-sources/00-deduplicated-master.json → domain | MIT compilation, inherits upstream obligations; **derived only**, not an independent vote |
| newedgex joined | newedgex/ani-manga-blocklist lists/joined-blacklist.txt | GPL-3.0; downloaded through the existing category downloader |

Also investigated: shosetsuorg/extensions on GitLab. No stable, directly verified public manifest with explicit site URLs was established, so it is *not* fetched or treated as an adapter. Add it only after confirming the format, maintenance and license; never infer website domains by scanning arbitrary Lua source.

The supported metadata language codes are en, it, ja, ko, zh (simplified/traditional tags share the language family), id, vi, th, es, pt, fr, de, ru, tr, ar. No language is invented when absent. Content categories include anime, manga, manhwa, manhua, webtoon, light-novel, webnovel and scanlation, when declared; a reader-specific fallback is used when the source is nonspecific.

## Discovery, scoring and promotion

1. scripts/download_sources.py uses existing atomic downloads/manifests for anime-manga and anime-manga-extended separately; one failed fetch cannot leave a partial source file to be merged accidentally.
2. scripts/merge_hosts.py --category anime-manga builds curated Pro with reviewed seeds, exact-host exceptions and known shared infrastructure filtering.
3. scripts/anime_discovery.py retains the original bounded catalog parsers and site inspection. With `--crawl-web` it also invokes scripts/anime_crawler.py: a persistent SQLite URL frontier seeded from public indexes, permitted user-specified directories, optional Brave Search API results, and original catalogs. The crawler prioritizes relevant links (including unknown external hosts), follows them recursively to a configurable depth, inspects homepage metadata, body text, language and bounded same-origin sitemap paths, and may reuse Playwright Chromium for missing static metadata. All raw links and search hits remain **unverified leads**, not independent votes or release approvals.
4. Confidence is evidence-based: 100 for existing Pro, 95 for manually approved, 90 for two independent catalog families also in joined, 80 for two independent catalog families alone, 72 for independently discovered and thematically website-verified sites, 60 for a catalog-plus-joined site with inspected metadata, and 30 for weaker leads. Exclusions score 0. Independent families are deduplicated; mirrored/derived datasets and web links are **never** independent confirmations. These scores affect review order only, not approval.
5. A candidate ledger (downloads/anime-discovery/candidates.jsonl) and preliminary catalog-confirmed shortlist (eligible-extended.txt) are produced as **intermediate outputs**. Website-verified candidates can be proposed even when absent from all catalogs and joined. The SQLite frontier and crawl-report.json / crawl-report.md record visited URLs, discoveries, sources, category/language distributions, errors and remaining budgets. None of these artifacts are published directly.
6. scripts/propose_anime_sites.py prepares **one review PR** updating `seeds/anime-manga-extended.txt` for: two independent catalog families; one catalog plus joined and inspected homepage/sitemap; or independent web leads with inspected thematic website metadata. Sitemap-only and outbound-link-only leads do not qualify. The English PR table includes content categories, languages, discovery sources, verification method/evidence, status and score. Human review is mandatory; the maintainer can remove false positives before merging. Up to 100 new candidates are proposed per run.
7. The monthly release job reads **only the merged approved seeds from main**, unions them with Pro, and validates output/exception/growth rules. Unmerged artifacts and candidate reports have **no effect** on publication; catalog servers going offline cannot silently add domains. Raw joined still serves as a source integrity check and is never merged as a blocklist.

The weekly .github/workflows/anime-discovery.yml (Wednesday 04:30 UTC, or manually via GitHub Actions) restores an incremental SQLite cache, uploads crawl and review artifacts, tests proposed data and creates/updates `automation/anime-discovery` PR **only when new proposals exist**. The action preserves an existing review branch when human reviews or manual commits are detected. It needs repository Actions permission to create PRs, with `contents: write` and `pull-requests: write`. **Known issue:** the run on 2026-11-03 had fully succeeded until GitHub returned `GitHub Actions is not permitted to create or approve pull requests`. This is **not** a Python bug. Enable **Settings → Actions → General → Workflow permissions → Allow GitHub Actions to create and approve pull requests** (organizational policies may override it), or configure `ANIME_DISCOVERY_PR_TOKEN` as a PAT/GitHub App token with authorized Contents/Pull requests write access. When the action is denied, the job fails with an actionable summary and direct `compare/main...automation/anime-discovery?expand=1` link to manually open the PR from the already-pushed branch. This is deliberate: a successful discovery without its requested PR should not appear green. A `GITHUB_TOKEN`-created PR may not automatically trigger `pull_request` CI; use the authorized token if that matters. The monthly release.yml builds and publishes anime-manga-extended.txt and site-pack-anime-manga-extended.zip alongside Pro. **A releases/latest/download URL is not available until a release containing the new asset has been published**. The desktop catalog must not be considered deployable before that release and the remote Ace Server catalog are updated.

List outputs are deterministically sorted for identical inputs. Candidate reports record a date, and SQLite ZIPs include build timestamps; byte-for-byte ZIP reproducibility is not guaranteed.

## Local commands

From the ace-hosts repository root:

~~~bash
uv run python scripts/download_sources.py --category anime-manga
uv run python scripts/download_sources.py --category anime-manga-extended
uv run python scripts/merge_hosts.py --category anime-manga --no-progress
uv run playwright install chromium  # once, on local development machines
uv run python scripts/anime_discovery.py --refresh --scrape-sites --browser-fallback --crawl-web --crawl-requests 220 --crawl-seconds 600 --crawl-depth 3 --crawl-workers 4 --max-scrape-sites 60 --max-browser-sites 15
uv run python scripts/propose_anime_sites.py  # changes reviewed seed; commit only via PR
uv run python scripts/merge_hosts.py --category anime-manga-extended --no-progress
uv run python scripts/validate_category_outputs.py
uv run python scripts/build_site_packs.py --pack pack-anime-manga-pro --pack pack-anime-manga-extended
uv run pytest -q
uv run basedpyright
uv lock --check
~~~

--refresh forces fresh catalog HTTPS requests; without it a successful local JSON cache may be reused. Failed refreshes do not silently fall back to stale cache data. --output-dir, --downloads-dir, --hosts-dir, --cache-dir, --timeout, --approved-file, --whitelist and --exceptions support isolated local runs.

**Crawling**: `--crawl-web` enables a separate persistent, prioritized frontier at `downloads/anime-discovery/crawl-state.sqlite`. It supports max requests, wall time, depth, concurrency, pages/domain and an optional `--crawl-directory-url https://...` (repeatable). Public directory seeds are listed in scripts/anime_crawler.py, and `--no-crawl-public-indexes` disables them. The 15 required language families are covered, including Simplified and Traditional Chinese query variants; a bounded official Brave Search integration activates only when `ACE_DISCOVERY_BRAVE_API_KEY` is supplied. With no key, directory, catalog and recursive link discovery continue. `--crawl-query-templates` accepts a UTF-8 JSON object mapping language codes to patterns such as `{"it":"{topic} leggere {terms}"}`. Templates are bounded and expanded using the configured language/category vocabulary. No search-results-page scraping is performed. DNS resolves to public IPs only and HTTP connects to a pinned vetted address with original-host TLS verification; redirects and private IP targets are not followed. Robots.txt, per-host pacing, retries for transient 5xx/timeouts, byte limits and explicit budgets apply. Browser rendering is same-origin and bounded, but Chrome DNS pinning is not implemented; avoid browser fallback in especially sensitive deployment networks. No login, CAPTCHA, rate-limit evasion or restricted-content scraping is performed. Pages disallowed by robots, HTTP access controls or errors do not qualify. A 404 robots.txt is treated as unrestricted. SQLite preserves the frontier between jobs; automatic cache restoration in GitHub Actions makes each weekly run incremental.

## Manual review and exceptions

- seeds/anime-manga.txt: curated **Pro** additions, changed only after human review.
- seeds/anime-manga-extended.txt: **sole approval gate for Extended** (besides Pro). The automated PR proposes exact hostname lines, which take effect only after merge. Manual edits are also reviewed via PR. They must still pass all safety and exception checks.
- whitelist.txt: exact-host global exceptions for every category.
- exceptions/anime-manga.txt: Pro category exclusions, also inherited by Extended.
- exceptions/anime-manga-extended.txt: additional Extended-only exclusions.

Do not strip or automatically broaden a hostname to its registrable root: www.example.org and example.org are distinct DNS hostnames. Conversely, never publish a shared infrastructure suffix like github.com, amazonaws.com or cloudfront.net, even if mentioned in joined. Review SHARED_SUFFIXES in the discovery module as part of maintenance. Do not infer that a DNS lookup proves content relevance, or that a DNS failure proves a site is dead. The system does not promise exhaustive live DNS reachability checks.

The pipeline fails closed if source input is missing, joined contains fewer than 25,000 valid hostnames, unexpected growth occurs or Extended is not a superset of Pro. The weekly discovery accepts multiple independent catalog families or independently collected web evidence, and allows optional core-catalog enforcement for stricter checks. **The monthly release does not depend on live discovery or catalog availability**: it publishes only merged approvals committed in `main` (plus existing Pro). No automatic mass promotion from joined is permitted.

## Licenses, attribution and risks

ACE Hosts Python code is MIT. The output data combines **distinct third-party licenses**: UT1 is CC BY-SA 4.0, newedgex GPL-3.0, reader extension definitions may be Apache-2.0, MIT/Apache dual-licensed or subject to their own attribution terms. The MIT license on kuiti's compilation does **not** relicense its underlying datasets. Preserve repository attribution and independently assess notice, share-alike, database and redistribution obligations before distributing the generated assets. The source-license labels and exported candidate evidence are an audit aid, not a legal opinion or redistribution permission.

Extended can block additional sites or unrelated services hosted on multipurpose content sites. It is opt-in, not the default. A higher hostname count is not a success criterion; review false positives and regression tests before every public release.
