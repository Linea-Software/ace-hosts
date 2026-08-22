# ace-hosts — Project Overview

**ace-hosts** is a Python pipeline that downloads, merges, deduplicates and
splits host lists for DNS-based blocking. It reimplements the maintenance
tooling of the archived [`columndeeply/hosts`](https://github.com/columndeeply/hosts)
repository (a unified adult-content blocklist of 10M+ domains, published as
GitHub-friendly `<90 MB` chunks named `hosts00`, `hosts01`, ...).

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
| Runtime deps      | requests, httpx, tqdm, python-dotenv (exact `==` pins)|
| Dev deps          | pytest, basedpyright (exact `==` pins)                |
| CI                | GitHub Actions (compileall, basedpyright, pytest, smoke test) |

## Layout

```
ace-hosts/
├── pyproject.toml        # Project metadata, exact == pins, basedpyright config
├── uv.lock               # Deterministic lockfile — always committed
├── .env.example          # All configuration keys (copy to .env to override)
├── whitelist.txt         # Domains excluded from the merged list
├── scripts/              # The `scripts` package (installed as `scripts.*`)
│   ├── __init__.py
│   ├── utils.py          # Parsing, validation, config, logging, atomic writes
│   ├── download_sources.py  # Fetch upstream lists (retries + rate limiting)
│   ├── merge_hosts.py    # Clean, merge, dedupe, whitelist, sort (+ optional split)
│   └── split_hosts.py    # Split into <90MB chunks (hosts00, hosts01, ...)
├── tests/                # pytest suite — must never touch the network
├── hosts/                # Generated output — gitignored (only .gitkeep committed)
├── downloads/            # Raw downloaded lists — gitignored
└── .github/workflows/ci.yml
```

## Commands

Always use `uv` — never bare `pip`/`python` outside the project environment.

| Command                                       | Purpose                                   |
| --------------------------------------------- | ----------------------------------------- |
| `uv sync`                                     | Install project + dev group (setup)       |
| `uv lock` / `uv lock --check`                 | Update / verify the lockfile              |
| `uv run pytest`                               | Run the test suite                        |
| `uv run basedpyright`                         | Type check (must be 0 errors, 0 warnings) |
| `uv run python scripts/download_sources.py`   | Fetch source lists into `downloads/`      |
| `uv run python scripts/merge_hosts.py --split`| Merge → dedupe → sort → split             |
| `uv run python scripts/split_hosts.py --check`| Verify chunk sizes                        |
| `uv audit`                                    | Check lockfile against OSV advisories     |
| `uv add <package>`                            | Maintainers only: change a dependency pin |

Console scripts (`ace-hosts-download`, `ace-hosts-merge`, `ace-hosts-split`)
are installed as entry points into `scripts.*:main` — identical behavior to
`python scripts/x.py`.

## Pipeline

1. `download_sources.py` fetches each source into `downloads/` with retries,
   exponential backoff and a rate-limit delay. Failures are skipped, not fatal.
2. `merge_hosts.py` parses every file (hosts lines, bare domains, adblock
   syntax, wildcards), normalizes to `127.0.0.1 <domain>`, drops reserved
   entries, applies `whitelist.txt`, deduplicates, sorts, and writes
   `hosts/merged_hosts.txt` atomically.
3. `split_hosts.py` splits the merged file into `<90 MB` chunks named
   `hosts00`, `hosts01`, ... (zero-padded), repeating the header comments on
   every chunk so each chunk is usable standalone.

## Conventions

- **Dependency pins:** exact `==` versions in `pyproject.toml` (direct and
  dev). Refresh via `uv add <package>` + re-lock; verify with `uv audit`.
  `uv.lock` is always committed.
- **Type checking:** basedpyright must stay at 0 errors / 0 warnings.
  - `reportUnusedCallResult = false` in `[tool.basedpyright]` is intentional
    (argparse registration idiom) — do not re-enable without a reason.
  - Use `X | None` (never `typing.Optional`), `collections.abc.Iterable` /
    `Iterator`, and typed `argparse.Namespace` subclasses (`MergeArgs`,
    `SplitArgs`, `DownloadArgs`) to keep `reportAny` clean.
- **Dual-mode scripts:** every script must run BOTH directly
  (`python scripts/x.py`) and as an installed package / `python -m`. This is
  handled by the `__package__` bootstrap at the top of each script (inserts
  the project root into `sys.path` for direct execution, then imports
  `from scripts import ...`). Keep the bootstrap identical across the three
  scripts. Tests exercise only the package-import path.
- **Atomic writes:** all output goes through `utils.atomic_write_lines`
  (temp file in the same directory + `os.replace`). Never write output files
  in place.
- **Generated files:** `hosts/*` and `downloads/*` are gitignored. Never
  commit generated lists or edit them by hand; change sources,
  `whitelist.txt`, or the scripts instead.
- **Memory:** merged domains are held in a `set` in memory (≈1M domains ≈
  100–200 MB RAM). Stream input files line-by-line (`utils.iter_domains`);
  never load a whole source list into memory.
- **i18n:** none — this is a CLI project, all output is English.

## Known Pitfalls

- **`split_hosts.split_file` — `flush()` must reset `current_size` via
  `nonlocal`.** A regression here silently produces one chunk per line
  (29,000+ chunks for a 30 MB file). Guarded by
  `test_split_file_produces_small_sequential_chunks` (`len(chunks) <= 5`) and
  the round-trip tests — keep those assertions when refactoring.
- **Chunk size accounting** includes the repeated header bytes; lines are
  never split across chunks. A single line longer than the chunk limit is
  written anyway with a warning.
- **Windows:** shells do not expand globs — `resolve_input_files()` expands
  `--input` globs itself. Keep that behavior.
- **Sources go stale:** the built-in `DEFAULT_SOURCES` list needs periodic
  pruning (dead URLs are skipped at runtime, but the README table should stay
  accurate).
- **Local Python is 3.14** while `requires-python = ">=3.10"` — keep the code
  compatible with 3.10 (no 3.12+ syntax).

## Testing

- `uv run pytest` — 28 tests: parsing (`tests/test_utils.py`), source-list
  parsing (`tests/test_download_sources.py`), merge/whitelist/split
  guarantees (`tests/test_merge_split.py`).
- Tests never touch the network and never depend on `downloads/` contents
  (auto-discovery picks up whatever is in `downloads/`, so tests always pass
  `--input` explicitly).
- CI: `compileall` → `basedpyright` → `pytest` → a merge+split smoke test.

## Git

- Conventional commit prefixes: `feat:`, `fix:`, `refactor:`, `tests:`,
  `docs:`, `chore:`. Subject line ≤ 50 chars, imperative mood, no trailing
  period. Commit after finishing and verifying a task.
