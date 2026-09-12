"""Shared helpers for the ace-hosts pipeline.

The original columndeeply/hosts repository maintained its blocklist with two
shell scripts: ``cleanup.sh`` (strip comments/whitespace, normalize IPs,
remove whitelisted domains, deduplicate, sort) and ``merger.sh`` (merge the
clean lists with the main one and split it into 90 MB chunks). This module
reimplements the shared parts of that workflow in Python: parsing, validation,
configuration, logging and atomic writes.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import tempfile
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from pathlib import Path

import dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

RESERVED_DOMAINS: frozenset[str] = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "local",
        "broadcasthost",
        "ip6-localhost",
        "ip6-loopback",
        "ip6-localnet",
        "ip6-mcastprefix",
        "ip6-allnodes",
        "ip6-allrouters",
        "ip6-allhosts",
        "0.0.0.0",
        "::",
    }
)

_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9_-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9_-]{0,61}[a-z0-9])?)*$"
)

DEFAULTS: dict[str, str] = {
    "SOURCES": "",
    "DOWNLOAD_DIR": "downloads",
    "OUTPUT_DIR": "hosts",
    "MERGED_FILENAME": "merged_hosts.txt",
    "WHITELIST_FILE": "whitelist.txt",
    "INPUT_FILES": "",
    "SPLIT_CHUNK_MB": "90",
    "SPLIT_PREFIX": "hosts",
    "REMOVE_DUPLICATES": "true",
    "SORT_OUTPUT": "true",
    "NORMALIZE_IP": "127.0.0.1",
    "STRIP_COMMENTS": "true",
    "REQUEST_TIMEOUT": "30",
    "RETRIES": "3",
    "RETRY_BACKOFF": "2.0",
    "RATE_LIMIT_DELAY": "1.0",
    "USER_AGENT": "ace-hosts/0.1",
    "LOG_LEVEL": "INFO",
}

logger = logging.getLogger("ace-hosts")


def load_config(env_file: Path | None = None) -> dict[str, str]:
    """Load configuration from `.env` (if present) and the environment."""
    env_file = env_file or PROJECT_ROOT / ".env"
    if env_file.is_file():
        dotenv.load_dotenv(env_file, override=False)
    return {key: os.environ.get(key, default) for key, default in DEFAULTS.items()}


def config_bool(value: str) -> bool:
    """Parse a configuration value as a boolean."""
    return value.strip().lower() in {"1", "true", "yes", "on"}


def setup_logging(level: str = "INFO") -> None:
    """Configure the root logger once (idempotent)."""
    if logging.getLogger().handlers:
        return
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def is_valid_domain(domain: str) -> bool:
    """Return True if *domain* is a plausible hostname."""
    return bool(_DOMAIN_RE.match(domain))


def extract_domains(line: str) -> list[str]:
    """Extract all domains from a single hosts/adblock-style line."""
    line = line.strip()
    if not line or line.startswith("#"):
        return []
    if "#" in line:
        line = line.split("#", 1)[0].strip()
    if not line:
        return []
    if line.startswith("||"):
        line = line[2:].split("^", 1)[0]
    line = re.sub(r"^[a-z][a-z0-9+.-]*://", "", line, flags=re.IGNORECASE)
    line = line.split("/", 1)[0]
    line = line.lstrip("*.")
    if not line:
        return []

    domains: list[str] = []
    for token in line.split():
        token = token.strip(".,;")
        if not token:
            continue
        try:
            ipaddress.ip_address(token)
            continue
        except ValueError:
            pass
        domain = token.rstrip(".").lower()
        if domain in RESERVED_DOMAINS or not is_valid_domain(domain):
            continue
        domains.append(domain)
    return domains


def iter_domains(path: Path) -> Iterator[str]:
    """Yield every valid domain found in *path*, one at a time."""
    with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
        for line in handle:
            yield from extract_domains(line)


def sanitize_source_filename(url: str) -> str:
    """Turn a source URL into a safe local filename."""
    name = url.split("/")[-1] or "source"
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return name or "source"


def sanitize_source_name(name: str) -> str:
    """Sanitize a source label for safe use as one local filename."""
    sanitized = re.sub(r"[^A-Za-z0-9._-]", "_", name).strip(".")
    return sanitized or "source"


def human_size(num_bytes: float) -> str:
    """Format a byte count in a human-readable way."""
    for unit in ("B", "KiB", "MiB", "GiB"):
        if num_bytes < 1024 or unit == "GiB":
            if unit == "B":
                return f"{int(num_bytes)} B"
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} GiB"  # pragma: no cover


def hosts_header(
    entries: int,
    sources: Iterable[str],
    generated_by: str = "ace-hosts",
    category: str | None = None,
    source_licenses: Iterable[str] = (),
) -> list[str]:
    """Build the comment header for a merged hosts file."""
    lines = [
        f"# Title: ace-hosts {category} blocklist" if category else "# Title: ace-hosts merged blocklist",
        f"# Generated: {datetime.now(timezone.utc).isoformat()}",
        f"# Entries: {entries}",
    ]
    if category:
        lines.append(f"# Category: {category}")
    source_list = ", ".join(sources)
    if source_list:
        lines.append(f"# Sources: {source_list}")
    licenses = ", ".join(source_licenses)
    if licenses:
        lines.append(f"# Source licenses: {licenses}")
        lines.append("# Licensing: generated data retains upstream license obligations; see README.md")
    lines.append(f"# Generated by: {generated_by}")
    return lines


def chunk_header(index: int) -> list[str]:
    """Build the comment header repeated at the top of every split chunk."""
    return [f"# ace-hosts blocklist - chunk {index:02d}"]


def atomic_write_lines(path: Path, lines: Iterable[str]) -> None:
    """Write *lines* (each on its own line) to *path* atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            for line in lines:
                handle.write(line)
                handle.write("\n")
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
