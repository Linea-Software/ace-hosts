# ace-hosts — Project Overview

**ace-hosts** is a Python pipeline. It downloads, merges, removes duplicates from, and packages category-specific host lists for DNS-based blocking.

The project began as a new implementation of the adult-list tools in the archived [`columndeeply/hosts`](https://github.com/columndeeply/hosts) project.
It now builds separate curated categories for anime, manga, manhwa, manhua, messaging, gaming, social, news, forums, sports, and AI tools.
It also builds Ace Engine-compatible SQLite site-pack ZIPs.

This project is in the `ace-project` monorepo directory.
However, **ace-hosts is a separate Git repository** (`C:\Projects\ace-project\ace-hosts`).

The README contains a mandatory disclaimer.
It states that LLMs developed the entire repository.
Do not remove this disclaimer.

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
│   ├── validate_category_outputs.py # Streaming source/hosts/attribution release guard
│   └── validate_release_assets.py # 2 GiB release-asset guard
├── tests/                   # pytest suite — must never touch the network
├── hosts/                   # Generated output — gitignored
├── downloads/               # Raw category downloads — gitignored
└── .github/workflows/
```

## Commands

Always use `uv`.
Do not run bare `pip` or `python` outside the project environment.

| Command | Purpose |
| --- | --- |
| `uv sync` | Install project + dev group |
| `uv lock` / `uv lock --check` | Update / verify lockfile |
| `uv run pytest` | Run tests |
| `uv run basedpyright` | Type check; must be 0 errors, 0 warnings |
| `uv run python scripts/download_sources.py --all-categories` | Fetch every built-in category |
| `uv run python scripts/merge_hosts.py --all-categories` | Build every category as one merged file |
| `uv run python scripts/build_site_packs.py` | Build Ace Engine-compatible site-pack ZIPs |
| `uv run python scripts/validate_category_outputs.py` | Reject incomplete/stale/empty/invalid category outputs |
| `uv run python scripts/validate_release_assets.py hosts` | Fail if any release asset exceeds 2 GiB |
| `uv audit` | Check lockfile against OSV advisories |
| `uv add <package>` / `uv remove <package>` | Maintainers only: change direct dependencies |

The installation provides these console scripts: `ace-hosts-download`, `ace-hosts-merge`, and `ace-hosts-build-site-packs`.
Their entry points call `scripts.*:main`.

## Pipeline

1. `source_catalog.py` is the authoritative registry for built-in categories and sources.
2. `download_sources.py` downloads sources into `downloads/<category>/`.
   It uses retries, backoff, and rate limits.
   It first saves each response in a temporary file.
   It then publishes the response with an atomic operation.
   The `.ace-hosts-sources` manifest for each category lists only sources that succeeded in the current run.
3. `merge_hosts.py` reads the success manifest.
   It reads input files one line at a time.
   It applies `whitelist.txt`, removes duplicate domains, and sorts the results.
   It writes one `hosts/<category>.txt` file.
4. `validate_category_outputs.py` runs before site-pack generation.
   In one streaming pass, it checks current success manifests, entry counts, and sorted unique output.
   It also checks category exclusions and upstream-license attribution.
5. `build_site_packs.py` hashes merged domains with XXH3-64.
   It writes the exact Ace Engine SQLite schema with the `entries` and `pack_meta` tables.
   For each managed pack, it publishes one `site-pack-*.zip` file that contains `pack.db`.
6. `validate_release_assets.py` checks generated release files before GitHub publishes a release.
   It rejects files larger than 2 GiB.

## Conventions

- **Dependency versions:** Specify exact `==` versions in `pyproject.toml`.
  Use `uv add` or `uv remove` to update direct dependencies.
  Commit the updated `uv.lock`.
- **Type checking:** Keep basedpyright at 0 errors and 0 warnings.
  The `reportUnusedCallResult = false` setting in `[tool.basedpyright]` is intentional.
  Use `X | None`, `collections.abc.Iterable` / `Iterator`, and typed `argparse.Namespace` subclasses.
- **Script execution:** Executable scripts that import `scripts` must support direct execution and installed entry points.
  Keep the `__package__` bootstrap.
- **Atomic writes:** Write generated text through `utils.atomic_write_lines`.
  Stage downloads before you call `os.replace`.
- **Generated files:** Do not edit or commit generated `hosts/*` or `downloads/*` files.
- **Source metadata:** Keep built-in categories, URLs, and license labels in `scripts/source_catalog.py`.
  Do not distribute this metadata across CLI modules.
- **Memory use:** Read source files one line at a time.
  The normal duplicate-removal process keeps one set of domains in memory.
  Do not also load entire source files into memory.
- **Compatibility:** ace-engine and ace-blocker-site use the release asset names.
  Find all consumers in the other repositories before you rename `.txt` or `site-pack-*.zip` files.
- **Licensing:** MIT covers the repository source code.
  It does not automatically cover generated third-party datasets.
  Keep source-license metadata and documentation.
- **Internationalization:** There is no i18n support.
  CLI output is in English.

## Known Pitfalls

- **Site-pack compatibility:** Keep the schema and hash representation byte-for-byte compatible with ace-engine.
  Store XXH3-64 hashes as signed SQLite INTEGER values.
  Keep `format_version = 1.0`.
  Name the ZIP entry exactly `pack.db`.
- **Release size:** GitHub release assets must remain in single files.
  The release workflow must stop before publication if a generated file exceeds 2 GiB.
- **Windows globs:** Windows shells do not expand glob patterns.
  The `resolve_input_files()` function expands these patterns.
- **Source manifests:** Automatic merging must use the current success manifest.
  Do not reintroduce failed or removed sources from older runs.
- **Download directories:** Store each category under `downloads/<category>/`.
  Do not use a legacy flat adult-download fallback.
- **Stale sources:** Individual dead URLs do not stop the pipeline.
  Keep the source catalog and README current.
- **Python versions:** A local Python installation can be newer than Python 3.10.
  Do not use syntax that Python 3.10 cannot run.

## Testing

- `uv run pytest` tests parsing, source/catalog handling, and failed-download cleanup.
  It also tests category manifests, merge/whitelist behavior, and site-pack schema/hash compatibility.
  Tests cover release-size validation and round trips.
- Tests must not access the network.
  Tests must not depend on existing `downloads/` contents.
- `uv run basedpyright` must report 0 errors and 0 warnings.
- CI also runs compileall and a merge/site-pack smoke test.

## Git

- Use these conventional commit prefixes: `feat:`, `fix:`, `refactor:`, `tests:`, `docs:`, and `chore:`.
- Write a subject line of no more than 50 characters.
- Use the imperative mood.
- Do not end the subject line with a period.
- Commit after you complete and verify a task.

## Curated Free starter packs (2026-11-08)

`starter/gambling-starter.txt` and `starter/streaming-starter.txt` contain small, manually selected baseline domain lists.
These files are committed to the repository.
`build_site_packs.py` copies them to `hosts/<category>.txt` when it builds their Free pack ZIPs.
The release workflow publishes `hosts/*`.
Thus, the text lists and ZIPs are released together.

Keep both starter packs small and manually reviewed.
Keep them separate from the full Pro `gambling` and `streaming` categories.
Coordinate catalog changes with Ace Blocker desktop and Ace Server.
