# Anime/Manga discovery and review

ACE Hosts exposes two stable, independent site-pack IDs:

- **Pro**: pack-anime-manga-pro → anime-manga.txt. Curated UT1 manga + newedgex refined + reviewed local seeds/anime-manga.txt. The default option; no weak discovery hit is promoted to Pro.
- **Extended**: pack-anime-manga-extended → anime-manga-extended.txt. Optional broader list that always contains Pro, plus cross-ecosystem corroborated or explicitly maintainer-approved candidates. General-purpose services are excluded.

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
3. scripts/anime_discovery.py parses bounded HTTPS JSON catalogs and records normalized IDNA/Punycode host, site names when known, category, languages, origin repository, extension IDs, original field, license and detection date. Mirrored or derived aggregations are **not** independent confirmations.
4. Confidence is evidence-based: 100 for existing Pro, 95 for manually approved, 90 for two independent catalog families and joined, 80 for two independent catalog families alone, and 30 for weaker candidates. Excluded records score 0. No AI inference or heuristic name-based promotion.
5. A candidate ledger (downloads/anime-discovery/candidates.jsonl) and eligible shortlist (eligible-extended.txt) are produced as **intermediate outputs**. Weak or web-only results go to review, not the lists. Exclusion matches, reserved names and shared infrastructure are not published.
6. The monthly release job then merges Pro plus the eligible shortlist, checks for anomalous growth and subset/exclusion violations, validates outputs and generates SQLite packs with the existing build_site_packs.py.

The weekly .github/workflows/anime-discovery.yml has only read permissions and uploads review files as CI artifacts; it does not automatically commit, push or create public releases. The monthly release.yml builds and publishes anime-manga-extended.txt and site-pack-anime-manga-extended.zip alongside Pro. **A releases/latest/download URL is not available until a release containing the new asset has been published**. The desktop catalog must not be considered deployable before that release and the remote Ace Server catalog are updated.

List outputs are deterministically sorted for identical inputs. Candidate reports record a date, and SQLite ZIPs include build timestamps; byte-for-byte ZIP reproducibility is not guaranteed.

## Local commands

From the ace-hosts repository root:

~~~bash
uv run python scripts/download_sources.py --category anime-manga
uv run python scripts/download_sources.py --category anime-manga-extended
uv run python scripts/merge_hosts.py --category anime-manga --no-progress
uv run python scripts/anime_discovery.py --refresh
uv run python scripts/merge_hosts.py --category anime-manga-extended --no-progress
uv run python scripts/validate_category_outputs.py
uv run python scripts/build_site_packs.py --pack pack-anime-manga-pro --pack pack-anime-manga-extended
uv run pytest -q
uv run basedpyright
uv lock --check
~~~

--refresh forces fresh catalog HTTPS requests; without it a successful local JSON cache may be reused. Failed refreshes do not silently fall back to stale cache data. --output-dir, --downloads-dir, --hosts-dir, --cache-dir, --timeout, --approved-file, --whitelist and --exceptions support isolated local runs.

**Opt-in web discovery** is disabled by default. The --directory-url https://... option reads one robots-permitted public directory page and its bounded links; no recursive crawling. For multilingual search, configure ACE_DISCOVERY_BRAVE_API_KEY and --brave-search to use the vendor's official JSON search API. WEB_QUERIES defines one request per supported language, at least 1 second apart; --queries-file accepts an alternative JSON mapping from language code to query. The scraper does not bypass CAPTCHAs, login requirements or anti-bot protections and does not scrape HTML search engine results. Directory/search hits are *leads*, with no independent confidence votes; they require original catalogs or manual confirmation before promotion.

## Manual review and exceptions

- seeds/anime-manga.txt: curated **Pro** additions, changed only after human review.
- seeds/anime-manga-extended.txt: explicitly reviewed **Extended** additions; one exact hostname per line, documented in review/PR. They must still pass all safety and exception checks.
- whitelist.txt: exact-host global exceptions for every category.
- exceptions/anime-manga.txt: Pro category exclusions, also inherited by Extended.
- exceptions/anime-manga-extended.txt: additional Extended-only exclusions.

Do not strip or automatically broaden a hostname to its registrable root: www.example.org and example.org are distinct DNS hostnames. Conversely, never publish a shared infrastructure suffix like github.com, amazonaws.com or cloudfront.net, even if mentioned in joined. Review SHARED_SUFFIXES in the discovery module as part of maintenance. Do not infer that a DNS lookup proves content relevance, or that a DNS failure proves a site is dead. The system does not promise exhaustive live DNS reachability checks.

The pipeline fails closed if source input is missing, joined contains fewer than 25,000 valid hostnames, unexpected growth occurs or Extended is not a superset of Pro. The monthly release requires every core independent ecosystem (Mangayomi, Aidoku, Miru, LNReader) to succeed; the weekly report tolerates individual failures. No automatic mass promotion from joined is permitted. The current workflow needs sufficient independent catalogs to preserve correctness; upstream shutdowns may prevent a scheduled release until sources or procedures are reviewed.

## Licenses, attribution and risks

ACE Hosts Python code is MIT. The output data combines **distinct third-party licenses**: UT1 is CC BY-SA 4.0, newedgex GPL-3.0, reader extension definitions may be Apache-2.0, MIT/Apache dual-licensed or subject to their own attribution terms. The MIT license on kuiti's compilation does **not** relicense its underlying datasets. Preserve repository attribution and independently assess notice, share-alike, database and redistribution obligations before distributing the generated assets. The source-license labels and exported candidate evidence are an audit aid, not a legal opinion or redistribution permission.

Extended can block additional sites or unrelated services hosted on multipurpose content sites. It is opt-in, not the default. A higher hostname count is not a success criterion; review false positives and regression tests before every public release.
