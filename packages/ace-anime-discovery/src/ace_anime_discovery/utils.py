"""Independent domain parsing, atomic output and logging for discovery."""
from __future__ import annotations

import ipaddress
import logging
import os
import re
import tempfile
from collections.abc import Iterable, Iterator
from pathlib import Path

PROJECT_ROOT = Path(os.environ.get("ACE_HOSTS_ROOT", ".")).resolve()
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9_-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9_-]{0,61}[a-z0-9])?)*$"
)
_RESERVED = {"localhost.localdomain", "localhost", "broadcasthost"}


def is_valid_domain(domain: str) -> bool:
    return "." in domain and bool(_DOMAIN_RE.fullmatch(domain))


def iter_domains(path: Path) -> Iterator[str]:
    """Read ordinary domain lists, hosts entries and Adblock domain rules."""
    with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
        for line in handle:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            if line.startswith("||"):
                line = line[2:].split("^", 1)[0]
            line = re.sub(r"^[a-z][a-z0-9+.-]*://", "", line, flags=re.I)
            line = line.split("/", 1)[0].lstrip("*.")
            for token in line.split():
                domain = token.strip(".,;").lower().rstrip(".")
                if domain in _RESERVED or not is_valid_domain(domain):
                    continue
                try:
                    ipaddress.ip_address(domain)
                except ValueError:
                    yield domain


def atomic_write_lines(path: Path, lines: Iterable[str]) -> None:
    """Write complete output to a sibling temporary file, then replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            for line in lines:
                handle.write(line)
                handle.write("\n")
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def setup_logging(level: str = "INFO") -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO),
                            format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
