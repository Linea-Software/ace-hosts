"""Convert AdGuard's service registry into focused plain-domain category inputs.

The registry publishes DNS Adblock rules, not a plain hosts list. Only simple
hostname rules are safe to convert. Unsupported syntaxes are deliberately
ignored rather than accidentally turning filters or directives into hostnames.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import cast

from scripts import utils

ADGUARD_SERVICES_URL = "https://adguardteam.github.io/HostlistsRegistry/assets/services.json"
ADGUARD_HOMEPAGE = "https://github.com/AdguardTeam/HostlistsRegistry"

CATEGORY_GROUPS: dict[str, str] = {
    "gaming": "gaming",
    "social": "social_network",
    "gambling": "gambling",
    "streaming": "streaming",
    "dating": "dating",
    "shopping": "shopping",
    "messaging": "messenger",
    "ai-tools": "ai",
}

_SIMPLE_RULE = re.compile(r"^\|\|([a-zA-Z0-9_.-]+\.[a-zA-Z0-9_-]+)\^?$")

# These services are commonly shared across unrelated sites. Exclude even
# service-specific subdomains until reviewed for a category explicitly.
SHARED_INFRASTRUCTURE_SUFFIXES: tuple[str, ...] = (
    "amazonaws.com",
    "cloudfront.net",
    "googleapis.com",
    "googleusercontent.com",
    "google.com",
    "microsoft.com",
    "windows.com",
    "live.com",
    "azureedge.net",
    "akamaihd.net",
    "akamaized.net",
    "fastly.net",
    "github.io",
    "pages.dev",
    "qq.com",
)


def adguard_rule_domain(rule: object) -> str | None:
    """Return an exact DNS hostname for a simple blocking rule only."""
    if not isinstance(rule, str):
        return None
    match = _SIMPLE_RULE.fullmatch(rule)
    if match is None:
        return None
    domain = match.group(1).lower()
    if not utils.is_valid_domain(domain) or domain in utils.RESERVED_DOMAINS:
        return None
    if any(domain == suffix or domain.endswith("." + suffix) for suffix in SHARED_INFRASTRUCTURE_SUFFIXES):
        return None
    return domain


def extract_group_domains(payload: object, group: str) -> list[str]:
    """Take only service rules for the requested registry group."""
    if not isinstance(payload, dict):
        raise ValueError("AdGuard service registry has an unexpected structure")
    entries = cast(dict[str, object], payload).get("blocked_services")
    if not isinstance(entries, list):
        raise ValueError("AdGuard service registry has an unexpected structure")
    domains: set[str] = set()
    found_group = False
    for entry in cast(list[object], entries):
        if not isinstance(entry, dict):
            continue
        service = cast(dict[str, object], entry)
        if service.get("group") != group:
            continue
        found_group = True
        rules = service.get("rules")
        if not isinstance(rules, list):
            continue
        for rule in cast(list[object], rules):
            domain = adguard_rule_domain(rule)
            if domain is not None:
                domains.add(domain)
    if not found_group or not domains:
        raise ValueError(f"AdGuard service group {group!r} has no usable domain rules")
    return sorted(domains)


def normalize_registry_file(path: Path, category: str) -> None:
    """Atomically replace a staged registry JSON file with plain domains."""
    group = CATEGORY_GROUPS[category]
    with path.open("r", encoding="utf-8") as handle:
        payload = cast(object, json.load(handle))
    domains = extract_group_domains(payload, group)
    utils.atomic_write_lines(path, domains)
