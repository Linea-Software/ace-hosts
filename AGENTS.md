# ace-hosts — Project Overview

**ace-hosts** is a Python pipeline that downloads, merges, deduplicates and
packages category-specific host lists for DNS-based blocking. It began as a
reimplementation of the archived [`columndeeply/hosts`](https://github.com/columndeeply/hosts)
adult-list tooling and now builds separate compact adult, extended adult,
gaming, social, gambling, streaming, dating and shopping lists plus
Ace Engine-compatible SQLite site-pack ZIPs.

This project lives inside the `ace-project` monorepo directory but is a
**standalone git repository** (`C:\Projects\ace-project\ace-hosts`).
The README carries a mandatory disclaimer stating the repo was fully developed
by LLMs — do not remove it.

---

## Tech Stack

| Layer             | Technology                                            |
| ----------------- | ----------------------------------------------------- |
| Language          | Python 3.10+ (builtin generics, `X | None` syntax)    |
| Package manager   | uv (project interface; `uv.lock` committed)           |
| Build backend     | hatchling (wheel package = `scripts/`)                |
| Runtime deps      | requests, tqdm, python-dotenv, xxhash (exact `==` pins) |
| Dev deps          | pytest, basedpyright (exact `==` pins)                |
| CI                | GitHub Actions (compileall, basedpyright, pytest, smoke test) |

## Layout

```text
ace-hosts/
├── pyproject.toml
├── uv.lock
├── .env.example
├── whitelist.txt
├── scripts/
│   ├── __init__.py
│   ├── source_catalog.py    # Categories, upstream URLs and license metadata
│   ├── utils.py             # Parsing, validation, config, logging, atomic writes
│   ├── download_sources.py  # Atomic category downloads + success manifests
│   ├── merge_hosts.py       # Category merge/dedupe/whitelist/sort
│   ├── build_site_packs.py  # Ace-compatible SQLite pack.db ZIP generation
│   └── validate_release_assets.py # 2 GiB release-asset guard
├── tests/                   # pytest suite — must never touch the network
├── hosts/                   # Generated output — gitignored
├── downloads/               # Raw category downloads — gitignored
└── .github/workflows/
```

## Commands

Always use `uv` — never bare `pip`/`python` outside the project environment.

| Command | Purpose |
| --- | --- |
| `uv sync` | Install project + dev group |
| `uv lock` / `uv lock --check` | Update / verify lockfile |
| `uv run pytest` | Run tests |
| `uv run basedpyright` | Type check; must be 0 errors, 0 warnings |
| `uv run python scripts/download_sources.py --all-categories` | Fetch every built-in category |
| `uv run python scripts/merge_hosts.py --all-categories` | Build every category as one merged file |
| `uv run python scripts/build_site_packs.py` | Build Ace Engine-compatible site-pack ZIPs |
| `uv run python scripts/validate_release_assets.py hosts` | Fail if any release asset exceeds 2 GiB |
| `uv audit` | Check lockfile against OSV advisories |
| `uv add <package>` / `uv remove <package>` | Maintainers only: change direct dependencies |

Console scripts (`ace-hosts-download`, `ace-hosts-merge`,
`ace-hosts-build-site-packs`) are installed as entry points into `scripts.*:main`.

## Pipeline

1. `source_catalog.py` is the canonical built-in taxonomy/source registry.
2. `download_sources.py` fetches sources into `downloads/<category>/` with
   retries/backoff/rate limiting. Each response is staged then atomically
   published. A per-category `.ace-hosts-sources` manifest lists only sources
   that succeeded in the current run.
3. `merge_hosts.py` follows that manifest, parses inputs line-by-line, applies
   `whitelist.txt`, deduplicates/sorts, and writes one `hosts/<category>.txt`.
4. `build_site_packs.py` hashes merged domains with XXH3-64, writes the exact
   Ace Engine `entries`/`pack_meta` SQLite schema, and publishes one
   `site-pack-*.zip` containing `pack.db` for each managed pack.
5. `validate_release_assets.py` rejects any generated release file larger than
   2 GiB before GitHub release publication.

## Conventions

- **Dependency pins:** exact `==` versions in `pyproject.toml`; update through
  `uv add` / `uv remove` and commit `uv.lock`.
- **Type checking:** basedpyright must stay at 0 errors / 0 warnings.
  - `reportUnusedCallResult = false` in `[tool.basedpyright]` is intentional.
  - Use `X | None`, `collections.abc.Iterable` / `Iterator`, and typed
    `argparse.Namespace` subclasses.
- **Dual-mode scripts:** executable scripts that import the `scripts` package
  must run directly and as installed entry points; keep the `__package__` bootstrap.
- **Atomic writes:** generated text outputs go through
  `utils.atomic_write_lines`; downloads must also stage before `os.replace`.
- **Generated files:** never commit/edit generated `hosts/*` or `downloads/*`.
- **Source metadata:** built-in categories/URLs/license labels belong in
  `scripts/source_catalog.py`, not scattered through CLI modules.
- **Memory:** stream source files line-by-line. The normal dedupe path holds one
  set of domains in memory; do not load entire source files separately.
- **Compatibility:** release asset names are consumed by ace-engine and
  ace-blocker-site; trace cross-repo consumers before renaming `.txt` or
  `site-pack-*.zip` artifacts.
- **Licensing:** MIT covers this repository's source code, not automatically the
  third-party generated datasets. Keep source-license metadata/documentation.
- **i18n:** none — CLI output is English.

## Known Pitfalls

- Site-pack databases must stay byte-for-byte compatible at the schema/hash
  contract level with ace-engine: XXH3-64 hashes stored as signed SQLite INTEGERs,
  `format_version = 1.0`, and a ZIP entry named exactly `pack.db`.
- GitHub release assets are intentionally unsplit. The release workflow must fail
  before publication if any generated file is larger than 2 GiB.
- Windows shells do not expand globs; `resolve_input_files()` expands them.
- Automatic merging must prefer the success manifest so failed/removed sources
  from older runs cannot silently reappear.
- Category downloads are isolated under `downloads/<category>/`; there is no
  legacy flat adult-download fallback.
- Sources go stale; dead URLs are tolerated individually but the source catalog
  and README must be kept current.
- Local Python may be newer than the declared Python 3.10 minimum; do not use
  syntax unavailable on 3.10.

## Testing

- `uv run pytest` covers parsing, source/catalog handling, failed-download
  cleanup, category manifests, merge/whitelist behavior, site-pack schema/hash
  compatibility, release-size validation and round trips.
- Tests never touch the network or depend on existing `downloads/` contents.
- `uv run basedpyright` must report 0 errors and 0 warnings.
- CI additionally runs compileall and a merge/site-pack smoke test.

## Git

- Conventional commit prefixes: `feat:`, `fix:`, `refactor:`, `tests:`,
  `docs:`, `chore:`. Subject line ≤50 chars, imperative mood, no trailing
  period. Commit after finishing and verifying a task.
