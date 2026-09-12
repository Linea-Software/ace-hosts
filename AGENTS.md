# ace-hosts — Project Overview

**ace-hosts** is a Python pipeline that downloads, merges, deduplicates and
splits category-specific host lists for DNS-based blocking. It began as a
reimplementation of the archived [`columndeeply/hosts`](https://github.com/columndeeply/hosts)
adult-list tooling and now builds separate adult, gaming, social, gambling,
streaming, dating and shopping lists. Adult release assets keep the legacy
`merged_hosts.txt`, `hosts00`, `hosts01`, ... names; other categories use
category-prefixed assets.

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
| Runtime deps      | requests, tqdm, python-dotenv (exact `==` pins)       |
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
│   ├── merge_hosts.py       # Category merge/dedupe/whitelist/sort (+ split)
│   └── split_hosts.py       # Size-bounded chunks + stale-chunk cleanup
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
| `uv run python scripts/merge_hosts.py --all-categories --split` | Build every category and chunks |
| `uv run python scripts/split_hosts.py --check` | Verify legacy adult chunks |
| `uv audit` | Check lockfile against OSV advisories |
| `uv add <package>` / `uv remove <package>` | Maintainers only: change direct dependencies |

Console scripts (`ace-hosts-download`, `ace-hosts-merge`, `ace-hosts-split`)
are installed as entry points into `scripts.*:main`.

## Pipeline

1. `source_catalog.py` is the canonical built-in taxonomy/source registry.
2. `download_sources.py` fetches sources into `downloads/<category>/` with
   retries/backoff/rate limiting. Each response is staged then atomically
   published. A per-category `.ace-hosts-sources` manifest lists only sources
   that succeeded in the current run.
3. `merge_hosts.py` follows that manifest, parses inputs line-by-line, applies
   `whitelist.txt`, deduplicates/sorts, and writes one merged category file.
   Adult keeps `hosts/merged_hosts.txt`; other categories use
   `hosts/<category>.txt`.
4. `split_hosts.py` stages a complete new chunk set, publishes it, then removes
   stale numeric chunks. Adult chunks remain `hosts00`, `hosts01`, ...; other
   categories use `<category>-hosts00`, ... .

## Conventions

- **Dependency pins:** exact `==` versions in `pyproject.toml`; update through
  `uv add` / `uv remove` and commit `uv.lock`.
- **Type checking:** basedpyright must stay at 0 errors / 0 warnings.
  - `reportUnusedCallResult = false` in `[tool.basedpyright]` is intentional.
  - Use `X | None`, `collections.abc.Iterable` / `Iterator`, and typed
    `argparse.Namespace` subclasses.
- **Dual-mode scripts:** executable scripts must run directly and as installed
  package entry points. Keep the `__package__` bootstrap on the three CLI files.
- **Atomic writes:** generated text outputs go through
  `utils.atomic_write_lines`; downloads must also stage before `os.replace`.
- **Generated files:** never commit/edit generated `hosts/*` or `downloads/*`.
- **Source metadata:** built-in categories/URLs/license labels belong in
  `scripts/source_catalog.py`, not scattered through CLI modules.
- **Memory:** stream source files line-by-line. The normal dedupe path holds one
  set of domains in memory; do not load entire source files separately.
- **Compatibility:** never rename the adult release assets without tracing and
  updating ace-engine/ace-app consumers first.
- **Licensing:** MIT covers this repository's source code, not automatically the
  third-party generated datasets. Keep source-license metadata/documentation.
- **i18n:** none — CLI output is English.

## Known Pitfalls

- `split_hosts.split_file` must reset `current_size` after every flush. The
  packing regression is guarded by `test_split_file_produces_small_sequential_chunks`.
- Chunk-size accounting includes all repeated headers and must use the actual
  chunk index (header width grows beyond chunk 99).
- Split rebuilds must remove stale old numeric chunks only after the new set has
  been staged successfully.
- Windows shells do not expand globs; `resolve_input_files()` expands them.
- Automatic merging must prefer the success manifest so failed/removed sources
  from older runs cannot silently reappear.
- The adult category accepts the old flat `downloads/*` layout only as a
  backward-compatibility fallback when `downloads/adult/` is absent.
- Sources go stale; dead URLs are tolerated individually but the source catalog
  and README must be kept current.
- Local Python may be newer than the declared Python 3.10 minimum; do not use
  syntax unavailable on 3.10.

## Testing

- `uv run pytest` covers parsing, source/catalog handling, failed-download
  cleanup, category manifests, merge/whitelist behavior, chunk size/naming,
  stale-chunk cleanup and round trips.
- Tests never touch the network or depend on existing `downloads/` contents.
- `uv run basedpyright` must report 0 errors and 0 warnings.
- CI additionally runs compileall and a merge+split smoke test.

## Git

- Conventional commit prefixes: `feat:`, `fix:`, `refactor:`, `tests:`,
  `docs:`, `chore:`. Subject line ≤50 chars, imperative mood, no trailing
  period. Commit after finishing and verifying a task.
