# ace-hosts

> **Disclaimer:** This repository — including all scripts, tests, and
> documentation — was fully developed by AI language models (LLMs). It has
> been reviewed and verified with automated tests and a type checker, but it
> should be treated accordingly. Please report any issues you find.

**Category-specific DNS blocklists for distracting and addictive websites, rebuilt monthly.**

`ace-hosts` downloads, normalizes, merges, deduplicates and packages upstream
host/domain lists into ready-to-use hosts files and Ace Engine site-pack ZIPs. It started as a successor to
the maintenance tooling of the deleted
[`columndeeply/hosts`](https://github.com/columndeeply/hosts) adult blocklist,
but now builds independent lists for:

- **adult** — compact pornography / NSFW list for the default/free pack
- **adult-extended** — full multi-source adult list for the extended/Pro pack
- **gaming** — online games, gaming portals and major gaming services
- **social** — social networks and major social/messaging platforms
- **gambling** — betting, casinos and gambling sites
- **streaming** — video/audio streaming and entertainment services
- **dating** — dating and matchmaking sites
- **shopping** — online shopping and commerce sites

The categories stay separate end-to-end: sources download into
`downloads/<category>/`, each merge consumes only that category's successful
source manifest, and each release asset has an unambiguous category name.

Every category is published as one merged file, for example `adult.txt`,
`adult-extended.txt`, `gaming.txt`, and `social.txt`. Ace-managed categories also
produce one SQLite backup ZIP such as `site-pack-adult-free.zip` or
`site-pack-gaming-free.zip`. Chunked release assets are no longer produced.

## Features

- **Eight behavioural categories** with a central source catalog in
  `scripts/source_catalog.py`.
- **Format-tolerant parsing** for classic hosts files, bare domains, Adblock
  syntax (`||domain^`), wildcards, URLs, inline comments, BOMs and CRLF.
- **Atomic downloads and outputs** — a failed refresh cannot leave a partial
  source file that is consumed by the next merge.
- **Stale-source protection** — each download writes a manifest of sources that
  succeeded during that run; automatic merging follows the manifest rather
  than blindly merging old files left in the directory.
- **Global whitelist support** via `whitelist.txt`, plus exact-domain **category exceptions** via `exceptions/<category>.txt`.
- **Deterministic output** through deduplication and sorting.
- **Whole-file releases** — merged lists and site-pack ZIPs are never split;
  release validation fails if any generated asset exceeds 2 GiB.
- **Deterministic environment** through exact direct dependency pins and the
  committed `uv.lock`.

## Requirements

- [uv](https://docs.astral.sh/uv/)
- Python 3.10+

## Setup

```bash
git clone https://github.com/Linea-Software/ace-hosts
cd ace-hosts
uv sync
```

## Quick start

Build every category:

```bash
uv run python scripts/download_sources.py --all-categories
uv run python scripts/merge_hosts.py --all-categories
uv run python scripts/build_site_packs.py
```

Or build one category:

```bash
uv run python scripts/download_sources.py --category gaming
uv run python scripts/merge_hosts.py --category gaming
```

The default remains `adult`, so the original commands still work:

```bash
uv run python scripts/download_sources.py
uv run python scripts/merge_hosts.py
```

Installed console commands are equivalent:

```bash
ace-hosts-download --all-categories
ace-hosts-merge --all-categories
ace-hosts-build-site-packs
```

Typical output:

```text
hosts/
├── adult.txt
├── adult-extended.txt
├── gaming.txt
├── social.txt
├── gambling.txt
├── streaming.txt
├── dating.txt
├── shopping.txt
├── site-pack-adult-free.zip
├── site-pack-adult-pro.zip
├── site-pack-gaming-free.zip
├── site-pack-social-free.zip
├── site-pack-gambling-pro.zip
├── site-pack-streaming-pro.zip
├── site-pack-shopping-pro.zip
└── sha256_checksums.txt   # release workflow
```

Each `site-pack-*.zip` contains exactly one `pack.db` SQLite database.

## Pipeline

```mermaid
flowchart LR
    A[Built-in source catalog] --> B[download_sources.py]
    B --> C[downloads/category + success manifest]
    C --> D[merge_hosts.py]
    D --> E[dedupe + global whitelist + category exceptions + sort]
    E --> F[category merged file]
    F --> G[build_site_packs.py]
    G --> H[site-pack ZIP with pack.db]
    F --> I[validate_release_assets.py]
    H --> I
```

## CLI

### `download_sources.py`

| Flag | Description |
| --- | --- |
| `--category NAME` | Download one built-in category; default `adult` |
| `--all-categories` | Download all built-in categories |
| `--sources LIST` | Override sources for one category with comma/newline-separated URLs or `name=url` entries |
| `--output-dir PATH` | Base raw-download directory; category subdirectories are created below it |
| `--dry-run` | Print selected sources without downloading |
| `--no-rate-limit` | Skip the delay between upstream requests |

`--sources`/`SOURCES` is intentionally single-category only. Custom source
names are sanitized before they are used as local filenames.

### `merge_hosts.py`

| Flag | Description |
| --- | --- |
| `--category NAME` | Merge one category; default `adult` |
| `--all-categories` | Merge every built-in category |
| `--input PATH` | Explicit input file/glob, repeatable; single-category mode only |
| `--output PATH` | Explicit merged output; single-category mode only |
| `--whitelist PATH` | Global domains to exclude from every category |
| `--exceptions-dir PATH` | Directory containing `<category>.txt` exact-domain exceptions |
| `--no-dedupe` | Keep duplicates |
| `--no-sort` | Preserve merge order |
| `--normalize-ip IP` | Hosts-file IP prefix; default `127.0.0.1` |
| `--no-progress` | Disable progress bars |

Automatic input selection prefers the downloader's success manifest, then
falls back to files under `downloads/<category>/`.

`whitelist.txt` is intentionally global. Category-specific false positives
belong in `exceptions/<category>.txt`; only exact hostnames are removed, so an
exception for `sub.example.com` does not implicitly exempt `example.com` or
other subdomains.

### `build_site_packs.py`

Builds Ace Engine-compatible SQLite backups from merged category files. Each
output is one `site-pack-*.zip` containing a single `pack.db`. The database uses
Ace's XXH3-64 host hashing convention and the engine's current `entries` and
`pack_meta` schema.

Use `--pack PACK_ID` to build a subset, `--hosts-dir PATH` to read merged inputs
from another directory, and `--output-dir PATH` to choose the ZIP destination.

### `validate_release_assets.py`

Validates the generated release directory and exits with an error when any file
is larger than 2 GiB. The release workflow runs this check before checksums and
publication.

## Built-in categories and sources

The catalog intentionally targets **behavioural/distraction blocking**, not a
generic malware/ad/tracker filter. Broad security lists are therefore not
mixed into these categories.

| Category | Built-in sources |
| --- | --- |
| adult | StevenBlack `porn-only`; HaGeZi NSFW |
| adult-extended | StevenBlack `porn-only`; Block List Project porn; 4skinSkywalker Anti-Porn; tiuxo porn; saskuu porno; StbanMc CommunityBlocklists porn; zangadoprojets Pornpages; HaGeZi NSFW; Sinfonietta pornography; chadmayfield porn; Energized Porn |
| gaming | UT1 `games`; Block List Project Fortnite |
| social | StevenBlack `social-only`; UT1 `social_networks`; Block List Project Facebook, TikTok, Twitter/X and WhatsApp |
| gambling | Block List Project gambling; StevenBlack `gambling-only`; UT1 `gambling` |
| streaming | UT1 `audio-video`; Block List Project YouTube |
| dating | UT1 `dating` |
| shopping | UT1 `shopping` |

Exact raw URLs, source homepages and license labels live in
[`scripts/source_catalog.py`](scripts/source_catalog.py), making source changes
reviewable in one place. The monthly job tolerates an individual dead source,
but fails if no selected source succeeds.

### Source families

- **UT1 / Université Toulouse Capitole** provides categorized domain data for
  games, gambling, social networks, audio/video, dating and shopping. The
  project publishes the data under CC BY-SA 4.0; `ace-hosts` consumes the daily
  GitHub mirror maintained by `olbat/ut1-blacklists`.
- **Block List Project** supplies dedicated category/service lists including
  gambling, Facebook, TikTok, Twitter/X, WhatsApp, YouTube and Fortnite.
- **StevenBlack/hosts** supplies separate extension-only lists for porn, social
  and gambling. `ace-hosts` deliberately uses those category-only variants —
  not StevenBlack's general adware/malware base list.
- The `adult` category is intentionally compact: StevenBlack's porn-only list
  plus HaGeZi's NSFW-only domains back the default/free Ace adult site pack.
- The `adult-extended` category retains the previous multi-source adult catalog
  for the extended/Pro site pack.

## Licensing of generated data

The **ace-hosts source code** in this repository is MIT-licensed. Generated
blocklist data is assembled from third-party datasets and is **not made MIT by
this repository's code license**. Upstream license and attribution/share-alike
obligations remain relevant to redistributed generated data.

Generated headers record the distinct source-license labels for built-in
categories. In particular, UT1 data is CC BY-SA 4.0, Block List Project is
published under the Unlicense, and other adult/StevenBlack sources have their
own upstream terms. Treat the labels as maintenance metadata and verify the
upstream repositories when making licensing decisions.

## Configuration

All keys are optional and may be supplied through `.env` or the environment.

| Key | Default | Description |
| --- | --- | --- |
| `SOURCES` | *(built-in category sources)* | Custom sources for one category |
| `DOWNLOAD_DIR` | `downloads` | Base raw-download directory |
| `OUTPUT_DIR` | `hosts` | Merged output directory |
| `MERGED_FILENAME` | `adult.txt` | Default adult merged filename |
| `WHITELIST_FILE` | `whitelist.txt` | Global domains excluded from every merge |
| `EXCEPTIONS_DIR` | `exceptions` | Per-category exact-domain exception files |
| `INPUT_FILES` | *(empty)* | Explicit single-category inputs/globs |
| `REMOVE_DUPLICATES` | `true` | Deduplicate domains |
| `SORT_OUTPUT` | `true` | Sort output |
| `NORMALIZE_IP` | `127.0.0.1` | Hosts-file IP prefix |
| `REQUEST_TIMEOUT` | `30` | Per-request timeout in seconds |
| `RETRIES` | `3` | Retries per source |
| `RETRY_BACKOFF` | `2.0` | Exponential-backoff base |
| `RATE_LIMIT_DELAY` | `1.0` | Delay between source downloads |
| `USER_AGENT` | `ace-hosts/0.1` | Download User-Agent |
| `LOG_LEVEL` | `INFO` | Logging level |

## Releases

The GitHub `Release` workflow runs on the first day of every month at 03:00
UTC. It downloads all categories, rebuilds the merged files and Ace site-pack
ZIPs, rejects any asset larger than 2 GiB, creates `sha256_checksums.txt`, and
publishes every output under a `vYYYY.MM.DD` tag.

Release assets expose whole merged lists plus ready-to-consume Ace site packs:

```text
https://github.com/Linea-Software/ace-hosts/releases/latest/download/adult.txt
https://github.com/Linea-Software/ace-hosts/releases/latest/download/adult-extended.txt
https://github.com/Linea-Software/ace-hosts/releases/latest/download/gaming.txt
https://github.com/Linea-Software/ace-hosts/releases/latest/download/site-pack-adult-free.zip
https://github.com/Linea-Software/ace-hosts/releases/latest/download/site-pack-adult-pro.zip
https://github.com/Linea-Software/ace-hosts/releases/latest/download/site-pack-gaming-free.zip
```

## Development

```bash
uv sync
uv run pytest
uv run basedpyright
uv run python -m compileall scripts tests
```

Tests never require network access. Source URL health is a maintenance concern,
not a unit-test dependency.

## Contributing

- **False positives / missing domains:** open an issue or PR.
- **New category/source:** edit `scripts/source_catalog.py` and document the
  upstream license/homepage.
- **Global whitelist:** use `whitelist.txt` only for domains that should never appear in any category.
- **Category false positive:** add the exact hostname to `exceptions/<category>.txt`.
- **Generated files:** do not edit `hosts/*` or `downloads/*` by hand.

## License

MIT for the repository's own source code — see [LICENSE](LICENSE). Third-party
blocklist data retains its upstream licensing terms; see the licensing section
above and the source catalog.

**Credits:** the original adult-list maintenance approach derives from the
archived `columndeeply/hosts` project. Category data is provided by the
respective upstream maintainers listed above.
