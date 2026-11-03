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
3. scripts/anime_discovery.py parses bounded HTTPS JSON catalogs and records normalized IDNA/Punycode host, site names when known, category, languages, origin repository, extension IDs, original field, license and detection date. With `--scrape-sites`, it also visits a bounded selection of catalog-nominated **real anime/manga site homepages**, reads HTML title/description/language and selected outbound links, and respects robots.txt. This is non-recursive, HTTPS-only and never follows redirects. Mirrored or derived aggregations, scraped metadata and external links are **not** independent catalog confirmations.
4. Confidence is evidence-based: 100 for existing Pro, 95 for manually approved, 90 for two independent catalog families and joined, 80 for two independent catalog families alone, and 30 for weaker candidates. Excluded records score 0. No AI inference or heuristic name-based promotion.
5. A candidate ledger (downloads/anime-discovery/candidates.jsonl) and preliminary eligible shortlist (eligible-extended.txt) are produced as **intermediate outputs**. The shortlist **is not published directly**. Weak web-only results, reserved hosts and shared infrastructure never pass to a release.
6. scripts/propose_anime_sites.py prepares **one review PR** updating `seeds/anime-manga-extended.txt`: either at least two original catalog families, or one catalog family plus matching joined evidence **and scraped thematic homepage metadata**. The PR shows evidence; the maintainer can remove false positives before merging. Up to 100 new candidates are proposed per run.
7. The monthly release job reads **only the merged approved seeds from main**, unions them with Pro, and validates output/exception/growth rules. Unmerged artifacts and candidate reports have **no effect** on publication; catalog servers going offline cannot silently add domains. Raw joined still serves as a source integrity check and is never merged as a blocklist.

The weekly .github/workflows/anime-discovery.yml (Wednesday 04:30 UTC, or manually via GitHub Actions) uploads review artifacts, tests proposed data and creates/updates `automation/anime-discovery` PR **only when new proposals exist**. It needs repository Actions permission to create PRs, with `contents: write` and `pull-requests: write`; for org policies or to trigger CI automatically on bot PRs, configure `ANIME_DISCOVERY_PR_TOKEN` as a PAT/GitHub App token with those permissions. Otherwise `GITHUB_TOKEN` can be used if the repository allows GitHub Actions PR creation (bot-created PRs with the default token do not automatically trigger `pull_request` CI). The monthly release.yml builds and publishes anime-manga-extended.txt and site-pack-anime-manga-extended.zip alongside Pro. **A releases/latest/download URL is not available until a release containing the new asset has been published**. The desktop catalog must not be considered deployable before that release and the remote Ace Server catalog are updated.

List outputs are deterministically sorted for identical inputs. Candidate reports record a date, and SQLite ZIPs include build timestamps; byte-for-byte ZIP reproducibility is not guaranteed.

## Local commands

From the ace-hosts repository root:

~~~bash
uv run python scripts/download_sources.py --category anime-manga
uv run python scripts/download_sources.py --category anime-manga-extended
uv run python scripts/merge_hosts.py --category anime-manga --no-progress
uv run python scripts/anime_discovery.py --refresh --scrape-sites --max-scrape-sites 60
uv run python scripts/propose_anime_sites.py  # changes reviewed seed; commit only via PR
uv run python scripts/merge_hosts.py --category anime-manga-extended --no-progress
uv run python scripts/validate_category_outputs.py
uv run python scripts/build_site_packs.py --pack pack-anime-manga-pro --pack pack-anime-manga-extended
uv run pytest -q
uv run basedpyright
uv lock --check
~~~

--refresh forces fresh catalog HTTPS requests; without it a successful local JSON cache may be reused. Failed refreshes do not silently fall back to stale cache data. --output-dir, --downloads-dir, --hosts-dir, --cache-dir, --timeout, --approved-file, --whitelist and --exceptions support isolated local runs.

**Site scraping** is enabled in the discovery workflow using `--scrape-sites` and scans up to 60 catalog-nominated public homepages; there are hard limits on sites/pages, response size and timeout. A resolved private/non-global IP is rejected and every successful homepage must respect robots.txt; 404 robots is treated as unrestricted according to robots conventions. No login, CAPTCHA bypass, redirect chasing, recursive crawling or HTML search-result scraping. Some sites will not be reachable; a failed scrape cannot qualify a single-catalog site for the PR. A robots rule can forbid collection. **Optional discovery** via `--directory-url https://...` reads one robots-permitted public directory page and its bounded links. Multilingual Brave Search is opt-in via `ACE_DISCOVERY_BRAVE_API_KEY` and `--brave-search`. Directory/search/outbound hits are *leads*, not independent votes and never enter a release without explicit review and merge.

## Manual review and exceptions

- seeds/anime-manga.txt: curated **Pro** additions, changed only after human review.
- seeds/anime-manga-extended.txt: **sole approval gate for Extended** (besides Pro). The automated PR proposes exact hostname lines, which take effect only after merge. Manual edits are also reviewed via PR. They must still pass all safety and exception checks.
- whitelist.txt: exact-host global exceptions for every category.
- exceptions/anime-manga.txt: Pro category exclusions, also inherited by Extended.
- exceptions/anime-manga-extended.txt: additional Extended-only exclusions.

Do not strip or automatically broaden a hostname to its registrable root: www.example.org and example.org are distinct DNS hostnames. Conversely, never publish a shared infrastructure suffix like github.com, amazonaws.com or cloudfront.net, even if mentioned in joined. Review SHARED_SUFFIXES in the discovery module as part of maintenance. Do not infer that a DNS lookup proves content relevance, or that a DNS failure proves a site is dead. The system does not promise exhaustive live DNS reachability checks.

The pipeline fails closed if source input is missing, joined contains fewer than 25,000 valid hostnames, unexpected growth occurs or Extended is not a superset of Pro. The weekly discovery requires multiple independent catalogs, and allows optional core-catalog enforcement for stricter checks. **The monthly release does not depend on live discovery or catalog availability**: it publishes only merged approvals committed in `main` (plus existing Pro). No automatic mass promotion from joined is permitted.

## Licenses, attribution and risks

ACE Hosts Python code is MIT. The output data combines **distinct third-party licenses**: UT1 is CC BY-SA 4.0, newedgex GPL-3.0, reader extension definitions may be Apache-2.0, MIT/Apache dual-licensed or subject to their own attribution terms. The MIT license on kuiti's compilation does **not** relicense its underlying datasets. Preserve repository attribution and independently assess notice, share-alike, database and redistribution obligations before distributing the generated assets. The source-license labels and exported candidate evidence are an audit aid, not a legal opinion or redistribution permission.

Extended can block additional sites or unrelated services hosted on multipurpose content sites. It is opt-in, not the default. A higher hostname count is not a success criterion; review false positives and regression tests before every public release.
