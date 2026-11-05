"""Guard completeness and identity of the built-in behavioral catalog."""

from scripts import source_catalog
from scripts.build_site_packs import SITE_PACKS


def test_every_release_category_has_unique_sources_and_description():
    assert len(source_catalog.category_names()) == 14
    for category in source_catalog.category_names():
        assert source_catalog.CATEGORY_DESCRIPTIONS[category]
        sources = source_catalog.CATEGORY_SOURCES[category]
        assert len(sources) > 0
        assert len({source.name for source in sources}) == len(sources)
        assert all(source.homepage.startswith("https://") for source in sources)


def test_packs_are_unique_and_mapped_to_known_categories():
    ids = [pack.pack_id for pack in SITE_PACKS]
    assets = [pack.asset_name for pack in SITE_PACKS]
    assert len(ids) == len(set(ids))
    assert len(assets) == len(set(assets))
    assert all(pack.category in source_catalog.category_names() for pack in SITE_PACKS)
    assert {pack.category for pack in SITE_PACKS} == set(source_catalog.category_names())
    assert len(SITE_PACKS) == 14
