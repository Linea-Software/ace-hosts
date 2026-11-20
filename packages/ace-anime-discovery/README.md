# ace-anime-discovery

Independent Python distribution for evidence-led anime, manga, manhwa, manhua, webtoon and novel website discovery. The distribution lives in the ace-hosts uv workspace, but its runtime modules, CLI and domain/file helpers do not import ace-hosts internals.

## CLI

From the `ace-hosts` repository root:

```sh
uv sync --locked
uv run ace-anime-discover --refresh --crawl-web \
  --crawl-requests 8000 --crawl-seconds 4500 --crawl-workers 12 \
  --crawl-seed-hosts 1400 --crawl-joined-hosts 1800
uv run ace-anime-propose
```

The pipeline loads the currently downloaded catalogs and joined input produced by `ace-hosts` as optional discovery hints and for release-baseline comparisons. **A newly discovered site does not need to appear in any third-party list or extension catalog to qualify for a review proposal.** Paths can be overridden with CLI arguments. If invoked outside that directory, set `ACE_HOSTS_ROOT` or pass absolute paths for each input/output. The original `scripts/anime_*.py` import paths and discovery/proposal CLI scripts remain supported for compatibility.

## Design

- Independent extension catalogs and optional official Brave Search API supply leads. Brave queries rotate through category/language combinations using a persistent cursor. An API key is optional and may incur separate provider costs.
- A persistent SQLite frontier interleaves hosts rather than repeatedly processing hundreds of links from one site. It checkpoints work, expires evidence, handles failed requests conservatively, schedules monthly rechecks and upgrades legacy state databases. Older indiscriminate outbound backlogs and stale HTML evidence from previous verification policies are invalidated on upgrade.
- The unreviewed `newedgex-joined.txt` file is sampled via a persistent cursor. The joined-list cursor resets on upstream SHA-256 changes while preserving deduplicated hosts. A joined-only domain is **not** an independent catalog confirmation and is never exported to a published blocklist on that basis alone.
- HTTP requests require public DNS answers, pinned IP addresses, verified TLS, HTTPS, robots permission (including Crawl-delay and Request-rate), body limits, per-host spacing and bounded global requests/time. Denied/rate-limited robots policies are cached for the current run, and due verification receives fair scheduling alongside new leads. 401/403/429 are not bypassed or retried.
- When `robots.txt` is actually absent (404/410 or an explicit not-found HTML title), discovery may continue on public pages only. The crawler filters login/account/admin, authentication, API and service paths, private files, sensitive query keys, and API/auth subdomains before enqueueing and fetching URLs. These filters also apply to the opt-in browser fallback. Explicit robots disallows remain authoritative. Network, TLS, redirects, challenges and temporary server failures are not treated as missing robots policies, and log distinct reasons.
- Playwright remains an opt-in fallback. It restricts page requests to the same host, but **does not enforce DNS pinning**; hosted CI therefore does not enable it.
- Theme detection uses bounded title, description and heading evidence, with Latin-word boundaries. Arbitrary body keywords, JSON-LD and generic outbound links cannot by themselves validate a site's genre.
- **Web-original sites:** the crawler can discover a completely unlisted site through web search or topic-focused links, inspect its thematic homepage, follow its own content routes (`/manga/title`, `/series/title`, etc.) and verify at least **two distinct internal pages with distinct content titles** by successful HTML fetch. Such sites are eligible for the *review PR*, even with zero independent catalogs and no joined-list match. Two query variants of the same path are not two pages; a sitemap URL by itself is not proof.
- **Catalog-supported sites:** a candidate can qualify through two independent extension-catalog families; one catalog family, a topical homepage, and a joined-list match; or one catalog family, a topical homepage, and at least two fetched content pages with distinct titles. Catalog provenance and on-site verification are separate checks. A single self-reported topical homepage or unrelated outbound link does not qualify.
- Every eligible web-original or catalog-supported candidate is included in the same review PR, with **no proposal-count cap**. The PR description contains only summary counts; inspect `seeds/anime-manga.txt` in Files changed and the workflow artifacts for per-site evidence. These are evidence heuristics, not guarantees of topicality: a maintainer still has to verify every site before merging.
- Discovery produces one **`crawl-report.sqlite` artifact per run**, separate from the persistent `crawl-state.sqlite`. Indexed tables contain per-request HTTP status/error/latency, page decisions, candidate outcomes, missing evidence, source attribution and yields, and scoped current-run versus historical metrics. The report contains no URL query strings, response bodies or authentication headers; console logs report progress, summaries, and notable anomalies. Other local files like `candidates.jsonl` are implementation inputs, not separate GitHub artifacts.

## SQLite run report (schema v2)

The report is an **invocation-specific artifact**, not the resumable frontier database. It is created before crawling; `http_requests` and `page_events` are committed as requests finish, so a partial run retains diagnostics even if final candidate classification does not complete. It must not contain full URLs, query parameters, HTML bodies, cookies, or authentication information. Local `.temp/` databases are for analysis only and are ignored by Git.

- `http_requests.failure_code` separates timeouts, DNS/TLS/network failures, redirects, rate limits, access-denied responses and server errors. The original HTTP status/phase/attempt is retained.
- `page_events` stores bounded JSON arrays of **versioned codes** for the decision, positive content observations and negative signals; raw page text is intentionally absent.
- `candidates.qualification_path`, `eligibility_paths` and `eligibility_checks` record the considered alternative ways to qualify and each required condition's boolean result. A failed alternative path does *not* mean all its checks are globally mandatory.
- `missing_evidence` explains specific failed checks; `source_attribution` and `source_yield` summarize which discovery channels led to human-review-eligible candidates. Source attribution can overlap.
- `run_metrics` explicitly separates `current_run` (this invocation's visits and failures), `lifetime_state` (cumulative persistent frontier statistics), and `candidate_snapshot` (all current ledger candidates).

Reason codes in the new columns end with `_V1`; changing their meaning requires a new version. For example, inspect rejection pathways with `SELECT hostname, path_code, check_code FROM eligibility_checks WHERE passed=0`, or group operational failures with `SELECT failure_code, COUNT(*) FROM http_requests WHERE failure_code IS NOT NULL GROUP BY failure_code`. The original `candidates.decision_code` and `missing_evidence.missing_code` remain available for existing consumers.

Human approval remains mandatory before anything is released. The report is evidence for review, never a publication authorization.
- `candidates.jsonl` and the report contain untrusted leads. Only a human-reviewed merge of `seeds/anime-manga.txt` affects published `ace-hosts` site packs. Unrelated general-purpose hosts discovered in prior PRs are explicitly excluded in `exceptions/anime-manga.txt`.

## GitHub Actions

For the public repository, standard hosted runner minutes are not billed, although GitHub limits a single job to six hours. The discovery workflow runs twice weekly with a 75-minute crawl deadline (4,500 seconds), 8,000 HTTP requests and a 145-minute job timeout (including headroom for manually configured two-hour crawls). Counts are upper bounds, not throughput guarantees. It uses 12 workers with per-host delays; no scraping of search results pages or bypass of access controls.

Run `uv run pytest`, `uv run basedpyright`, and `uv lock --check` in the ace-hosts workspace before changes.
