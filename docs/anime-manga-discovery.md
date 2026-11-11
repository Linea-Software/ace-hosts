# Anime/Manga discovery and review

ACE Hosts publishes one Anime/Manga site pack: **pack-anime-manga-pro → anime-manga.txt**.
The pack combines UT1 manga, newedgex refined, and the reviewed entries in `seeds/anime-manga.txt`.
Anime Discovery proposes additions to this seed in a pull request (PR).
A maintainer must review the PR before the project publishes new candidates.

The newedgex joined dataset contains approximately 103,000 entries.
This dataset is **discovery evidence only**.
The downloader gets it with the other category sources.
The release process does not add it to the published hosts file.
A maintainer must approve candidates in a PR before they can enter the hosts file.
The process also excludes shared infrastructure.

## Upstream catalogs and adapter formats

The independent parser functions in scripts/anime_discovery.py read only declared site-home URLs.
They do not collect arbitrary URLs from extension source code, icons, authentication endpoints, APIs, or CDNs.

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

The team also examined shosetsuorg/extensions on GitLab.
It did not find a stable, directly verified public manifest with explicit site URLs.
Thus, the pipeline does not download this source or use it as an adapter.
Confirm its format, maintenance status, and license before you add it.
Do not search arbitrary Lua source code to guess website domains.

The supported metadata language codes are en, it, ja, ko, zh, id, vi, th, es, pt, fr, de, ru, tr, and ar.
Simplified and Traditional Chinese tags belong to the same zh language family.
Do not assign a language when the source does not give one.

Declared content categories include anime, manga, manhwa, manhua, webtoon, light-novel, webnovel, and scanlation.
If a source does not give a specific category, the pipeline uses a reader-specific fallback.

## Discovery, scoring and promotion

1. **Download sources.** scripts/download_sources.py uses atomic downloads and success manifests for anime-manga.
   It also downloads the joined dataset as discovery-only data.
   A failed download cannot leave a partial source for merging.

2. **Merge approved sources.** `scripts/merge_hosts.py --category anime-manga` builds one Pro pack.
   It uses regular upstream sources and reviewed seeds.
   It excludes joined-only candidates, exact-host exceptions, and shared infrastructure.

3. **Discover candidates.** scripts/anime_discovery.py uses the existing limited catalog parsers and site inspections.
   The `--crawl-web` option also starts scripts/anime_crawler.py.
   The crawler uses a persistent SQLite URL list with priority order.
   Public indexes, permitted user-specified directories, optional Brave Search API results, and original catalogs supply starting URLs.
   The crawler gives priority to relevant links, including links to unknown external hosts.
   It follows links recursively to the configured maximum depth.
   It examines homepage metadata, body text, language, and limited same-origin sitemap paths.
   It can also use Playwright Chromium when static metadata is missing.
   Treat all raw links and search results as **unverified leads**.
   They are not independent confirmations or release approvals.

4. **Score evidence.** Assign confidence scores by the evidence in this table.
   These scores determine review order.
   They do not approve a candidate.

   | Score | Evidence |
   | --- | --- |
   | 100 | Existing Pro host |
   | 95 | Manually approved host |
   | 90 | Two independent catalog families and a match in joined |
   | 80 | Two independent catalog families without joined |
   | 72 | Independent web discovery with verified website content relevant to the category |
   | 60 | One catalog family, a match in joined, and inspected metadata |
   | 30 | Weaker evidence |
   | 0 | Excluded host |

   Count each independent catalog family once.
   **Do not** count mirrored datasets, derived datasets, or web links as independent confirmations.

5. **Record candidates.** The pipeline writes `downloads/anime-discovery/candidates.jsonl` as a candidate ledger.
   It writes `eligible-anime.txt` as a preliminary list of candidates confirmed by catalogs.
   These files are **intermediate outputs**.
   The pipeline can also propose website-verified candidates absent from all catalogs and joined.
   The SQLite frontier preserves crawl state.
   `crawl-report.json` and `crawl-report.md` record visited URLs, discoveries, sources, category and language distributions, errors, and remaining budgets.
   Do not publish these intermediate files directly.

6. **Propose approved evidence for review.** scripts/propose_anime_sites.py creates **one review PR** for `seeds/anime-manga.txt`.
   A candidate qualifies through two independent catalog families.
   It also qualifies through one catalog, joined, and an inspected homepage or sitemap.
   Independent web leads qualify when inspected website metadata confirms the subject.
   Sitemap-only and outbound-link-only leads do not qualify.
   The English PR table shows content categories, languages, discovery sources, verification methods, evidence, status, and score.
   A person must review the changes.
   The maintainer can remove false positives before merging.
   Propose no more than 100 new candidates in one run.

7. **Publish reviewed data.** Each month, the release job merges reviewed seeds from main into the Pro file.
   It validates output, exceptions, and approved-source provenance.
   Unmerged files and candidate reports have **no effect** on publication.
   Offline catalog servers cannot cause the release process to add domains.
   The pipeline uses raw joined data only to check source integrity.
   It never merges the joined dataset as a blocklist.

## Weekly discovery and monthly release

The weekly workflow is `.github/workflows/anime-discovery.yml`.
It runs each Wednesday at 04:30 UTC.
You can also start it manually in GitHub Actions.

The workflow restores an incremental SQLite cache.
It uploads crawl and review artifacts and tests proposed data.
It creates or updates an `automation/anime-discovery` PR **only when new proposals exist**.
When the workflow detects human review or manual commits, it preserves the existing review branch.

The workflow needs permission to create PRs.
Give it `contents: write` and `pull-requests: write` permissions.

**Known issue (2026-11-03):** The workflow completed its other steps.
GitHub then returned `GitHub Actions is not permitted to create or approve pull requests`.
This was **not** a Python error.

To permit PR creation, enable **Settings → Actions → General → Workflow permissions → Allow GitHub Actions to create and approve pull requests**.
An organization policy can override this setting.
Alternatively, configure `ANIME_DISCOVERY_PR_TOKEN` with an authorized PAT or GitHub App token.
The token needs Contents and Pull requests write access.

If GitHub denies the action, the job fails and provides a summary.
It also provides a `compare/main...automation/anime-discovery?expand=1` link.
Use this link to open a PR manually from the branch that the workflow already pushed.
This failure is intentional.
A discovery run must not show success when it cannot create its required PR.
A PR created with `GITHUB_TOKEN` might not trigger `pull_request` CI.
Use the authorized token if that CI trigger is necessary.

For this category, the monthly `release.yml` publishes only `anime-manga.txt` and `site-pack-anime-manga-pro.zip`.
The `releases/latest/download` URL is not available until a release contains the new asset.
Do not treat the desktop catalog as deployable before that release is available.
The remote Ace Server catalog must also be updated.

For identical inputs, the pipeline sorts list outputs in the same order.
Candidate reports contain a date.
SQLite ZIPs contain build timestamps.
Thus, the pipeline does not guarantee byte-for-byte identical ZIP files.

## Local commands

Run these commands from the ace-hosts repository root:

~~~bash
uv run python scripts/download_sources.py --category anime-manga
uv run python scripts/merge_hosts.py --category anime-manga --no-progress
uv run playwright install chromium  # once, on local development machines
uv run python scripts/anime_discovery.py --refresh --scrape-sites --browser-fallback --crawl-web --crawl-requests 220 --crawl-seconds 600 --crawl-depth 3 --crawl-workers 4 --max-scrape-sites 60 --max-browser-sites 15
uv run python scripts/propose_anime_sites.py  # changes reviewed seed; commit only via PR
uv run python scripts/validate_category_outputs.py
uv run python scripts/build_site_packs.py --pack pack-anime-manga-pro
uv run pytest -q
uv run basedpyright
uv lock --check
~~~

The `--refresh` option forces new HTTPS requests to the catalogs.
Without this option, the pipeline can reuse a successful local JSON cache.
If refresh fails, the pipeline does not use stale cache data without notice.

These options support separate local runs:
`--output-dir`, `--downloads-dir`, `--hosts-dir`, `--cache-dir`, `--timeout`, `--approved-file`, `--whitelist`, and `--exceptions`.

## Web crawling

The `--crawl-web` option enables a separate persistent URL list at `downloads/anime-discovery/crawl-state.sqlite`.
The crawler assigns priorities to URLs.
You can limit requests, elapsed time, depth, concurrent work, and pages per domain.
Use `--crawl-directory-url https://...` to add a directory URL.
You can repeat this option.

The public directory starting points are in scripts/anime_crawler.py.
Use `--no-crawl-public-indexes` to disable them.
The crawler supports all 15 required language families.
This includes search terms for Simplified and Traditional Chinese.

The crawler can use the official Brave Search API within set limits.
It activates this integration only when `ACE_DISCOVERY_BRAVE_API_KEY` is available.
Without the key, directory, catalog, and recursive link discovery continue.

`--crawl-query-templates` accepts a UTF-8 JSON object.
The object maps language codes to patterns such as `{"it":"{topic} leggere {terms}"}`.
The crawler limits and expands these patterns with the configured language and category vocabulary.
It does not scrape search-results pages.

DNS resolution accepts public IP addresses only.
HTTP connects to a checked, fixed IP address and verifies TLS for the original hostname.
The crawler does not follow redirects or connect to private IP targets.
It follows robots.txt, per-host request intervals, response byte limits, and explicit work budgets.
It retries transient 5xx responses and timeouts.

Browser rendering stays on the same origin and has limits.
**Chrome DNS pinning is not implemented.**
Do not use browser fallback on deployment networks that require this protection.
The crawler does not log in, solve CAPTCHAs, evade rate limits, or scrape restricted content.
Do not accept pages that robots.txt, HTTP access rules, or errors prohibit.
Treat an HTTP 404 response for robots.txt as no restriction.

SQLite saves the URL list between jobs.
GitHub Actions restores the cache automatically.
Thus, each weekly run continues earlier discovery work.

## Manual review and exceptions

- `seeds/anime-manga.txt` contains curated **Pro** hosts.
  It is the only approval point for Anime Discovery proposals.
  A PR proposes exact hostname lines.
  These lines take effect only after merge.
  Apply all safety and exception checks to every change.
- `whitelist.txt` contains global exact-host exceptions for all categories.
- `exceptions/anime-manga.txt` contains category-wide exact-host exclusions.

Do not remove parts of a hostname or automatically change it to its registrable root.
For example, `www.example.org` and `example.org` are different DNS hostnames.
Never publish shared infrastructure suffixes such as `github.com`, `amazonaws.com`, or `cloudfront.net`.
This rule also applies when the joined dataset contains these suffixes.
Review `SHARED_SUFFIXES` in the discovery module during maintenance.

A DNS lookup does not prove that a website has relevant content.
A DNS failure does not prove that a website is no longer in service.
The system does not guarantee a complete live DNS reachability check.

If no publishable source is available, the pipeline stops without publishing.
Discovery rejects joined input with fewer than 25,000 valid hostnames.
Release validation rejects domains without approval.

Weekly discovery accepts evidence from multiple independent catalog families or independent web collection.
You can enable optional core-catalog enforcement for stricter checks.

**The monthly release does not depend on live discovery or catalog availability.**
It publishes merged approvals committed in `main`, together with existing Pro data.
Do not automatically add large groups of candidates from joined.

## Licenses, attribution and risks

The ACE Hosts Python source code has an MIT license.
The output data has **different third-party licenses**.
UT1 uses CC BY-SA 4.0.
newedgex uses GPL-3.0.
Reader extension definitions can use Apache-2.0, MIT/Apache dual licenses, or other attribution terms.
The MIT license on kuiti's compilation does **not** change the licenses of its source datasets.

Keep repository attribution.
Before you distribute generated assets, evaluate notice, share-alike, database, and redistribution requirements independently.
Source-license labels and exported candidate evidence help with audits.
They are not legal advice or permission to redistribute.

Newly reviewed entries can block more sites.
They can also block unrelated services on sites that provide different types of content.
A larger number of hostnames does not necessarily mean better results.
Review false positives and regression tests before each public release.
