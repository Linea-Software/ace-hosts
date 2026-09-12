"""Built-in category catalog and upstream source metadata for ace-hosts."""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_CATEGORY = "adult"
DOWNLOAD_MANIFEST = ".ace-hosts-sources"


@dataclass(frozen=True)
class Source:
    """One upstream blocklist source."""

    name: str
    url: str
    license: str
    homepage: str


CATEGORY_DESCRIPTIONS: dict[str, str] = {
    "adult": "Adult and pornographic websites",
    "gaming": "Online games, game portals, publishers and gaming services",
    "social": "Social networks and major social/messaging platforms",
    "gambling": "Gambling, betting and casino websites",
    "streaming": "Video/audio streaming and entertainment services",
    "dating": "Dating and matchmaking websites",
    "shopping": "Online shopping and commerce websites",
}

# Keep category sources deliberately focused. General ad/malware/tracker lists do
# not belong in behavioural-blocking categories because they cause surprising
# over-blocking and make a category's meaning impossible to reason about.
CATEGORY_SOURCES: dict[str, tuple[Source, ...]] = {
    "adult": (
        Source(
            "stevenblack-porn",
            "https://raw.githubusercontent.com/StevenBlack/hosts/master/alternates/porn-only/hosts",
            "mixed upstream licenses; see StevenBlack/hosts",
            "https://github.com/StevenBlack/hosts",
        ),
        Source(
            "blocklistproject-porn",
            "https://raw.githubusercontent.com/blocklistproject/Lists/master/porn.txt",
            "Unlicense",
            "https://github.com/blocklistproject/Lists",
        ),
        Source(
            "4skinskywalker",
            "https://raw.githubusercontent.com/4skinSkywalker/Anti-Porn-HOSTS-File/master/HOSTS.txt",
            "see upstream",
            "https://github.com/4skinSkywalker/Anti-Porn-HOSTS-File",
        ),
        Source(
            "tiuxo-porn",
            "https://raw.githubusercontent.com/tiuxo/hosts/master/porn",
            "CC BY 4.0",
            "https://github.com/tiuxo/hosts",
        ),
        Source(
            "saskuu-porno",
            "https://raw.githubusercontent.com/saskuu/blocklist/main/porno.txt",
            "see upstream",
            "https://github.com/saskuu/blocklist",
        ),
        Source(
            "stbanmc-porn",
            "https://raw.githubusercontent.com/StbanMc/CommunityBlocklists/main/exports/domains/porn.txt",
            "MIT",
            "https://github.com/StbanMc/CommunityBlocklists",
        ),
        Source(
            "zangadoprojets-porn",
            "https://raw.githubusercontent.com/zangadoprojets/pi-hole-blocklist/main/Pornpages.txt",
            "MIT",
            "https://github.com/zangadoprojets/pi-hole-blocklist",
        ),
        Source(
            "hagezi-nsfw",
            "https://cdn.jsdelivr.net/gh/hagezi/dns-blocklists@latest/wildcard/nsfw-onlydomains.txt",
            "GPL-3.0",
            "https://github.com/hagezi/dns-blocklists",
        ),
        Source(
            "sinfonietta-porn",
            "https://raw.githubusercontent.com/Sinfonietta/hostfiles/master/pornography-hosts",
            "MIT",
            "https://github.com/Sinfonietta/hostfiles",
        ),
        Source(
            "chadmayfield-porn",
            "https://raw.githubusercontent.com/chadmayfield/my-pihole-blocklists/master/lists/pi_blocklist_porn_all.list",
            "GPL-3.0",
            "https://github.com/chadmayfield/my-pihole-blocklists",
        ),
        Source(
            "energized-porn",
            "https://raw.githubusercontent.com/EnergizedProtection/EnergizedHosts/master/EnergizedPorn/energized/EnergizedPorn-domains.txt",
            "MIT",
            "https://github.com/EnergizedProtection/EnergizedHosts",
        ),
    ),
    "gaming": (
        Source(
            "ut1-games",
            "https://raw.githubusercontent.com/olbat/ut1-blacklists/master/blacklists/games/domains",
            "CC BY-SA 4.0",
            "https://dsi.ut-capitole.fr/blacklists/",
        ),
        Source(
            "blocklistproject-fortnite",
            "https://raw.githubusercontent.com/blocklistproject/Lists/master/fortnite.txt",
            "Unlicense",
            "https://github.com/blocklistproject/Lists",
        ),
    ),
    "social": (
        Source(
            "stevenblack-social",
            "https://raw.githubusercontent.com/StevenBlack/hosts/master/alternates/social-only/hosts",
            "mixed upstream licenses; see StevenBlack/hosts",
            "https://github.com/StevenBlack/hosts",
        ),
        Source(
            "ut1-social-networks",
            "https://raw.githubusercontent.com/olbat/ut1-blacklists/master/blacklists/social_networks/domains",
            "CC BY-SA 4.0",
            "https://dsi.ut-capitole.fr/blacklists/",
        ),
        Source(
            "blocklistproject-facebook",
            "https://raw.githubusercontent.com/blocklistproject/Lists/master/facebook.txt",
            "Unlicense",
            "https://github.com/blocklistproject/Lists",
        ),
        Source(
            "blocklistproject-tiktok",
            "https://raw.githubusercontent.com/blocklistproject/Lists/master/tiktok.txt",
            "Unlicense",
            "https://github.com/blocklistproject/Lists",
        ),
        Source(
            "blocklistproject-twitter",
            "https://raw.githubusercontent.com/blocklistproject/Lists/master/twitter.txt",
            "Unlicense",
            "https://github.com/blocklistproject/Lists",
        ),
        Source(
            "blocklistproject-whatsapp",
            "https://raw.githubusercontent.com/blocklistproject/Lists/master/whatsapp.txt",
            "Unlicense",
            "https://github.com/blocklistproject/Lists",
        ),
    ),
    "gambling": (
        Source(
            "blocklistproject-gambling",
            "https://raw.githubusercontent.com/blocklistproject/Lists/master/gambling.txt",
            "Unlicense",
            "https://github.com/blocklistproject/Lists",
        ),
        Source(
            "stevenblack-gambling",
            "https://raw.githubusercontent.com/StevenBlack/hosts/master/alternates/gambling-only/hosts",
            "mixed upstream licenses; see StevenBlack/hosts",
            "https://github.com/StevenBlack/hosts",
        ),
        Source(
            "ut1-gambling",
            "https://raw.githubusercontent.com/olbat/ut1-blacklists/master/blacklists/gambling/domains",
            "CC BY-SA 4.0",
            "https://dsi.ut-capitole.fr/blacklists/",
        ),
    ),
    "streaming": (
        Source(
            "ut1-audio-video",
            "https://raw.githubusercontent.com/olbat/ut1-blacklists/master/blacklists/audio-video/domains",
            "CC BY-SA 4.0",
            "https://dsi.ut-capitole.fr/blacklists/",
        ),
        Source(
            "blocklistproject-youtube",
            "https://raw.githubusercontent.com/blocklistproject/Lists/master/youtube.txt",
            "Unlicense",
            "https://github.com/blocklistproject/Lists",
        ),
    ),
    "dating": (
        Source(
            "ut1-dating",
            "https://raw.githubusercontent.com/olbat/ut1-blacklists/master/blacklists/dating/domains",
            "CC BY-SA 4.0",
            "https://dsi.ut-capitole.fr/blacklists/",
        ),
    ),
    "shopping": (
        Source(
            "ut1-shopping",
            "https://raw.githubusercontent.com/olbat/ut1-blacklists/master/blacklists/shopping/domains",
            "CC BY-SA 4.0",
            "https://dsi.ut-capitole.fr/blacklists/",
        ),
    ),
}


def category_names() -> tuple[str, ...]:
    """Return built-in category names in stable release order."""
    return tuple(CATEGORY_SOURCES)


def sources_for_category(category: str) -> dict[str, str]:
    """Return the built-in ``{name: url}`` mapping for *category*."""
    try:
        return {source.name: source.url for source in CATEGORY_SOURCES[category]}
    except KeyError as exc:  # defensive for programmatic callers
        raise ValueError(f"unknown category: {category}") from exc


def licenses_for_category(category: str) -> tuple[str, ...]:
    """Return distinct source license labels for *category*, preserving order."""
    seen: set[str] = set()
    licenses: list[str] = []
    for source in CATEGORY_SOURCES[category]:
        if source.license not in seen:
            seen.add(source.license)
            licenses.append(source.license)
    return tuple(licenses)
