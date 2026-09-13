"""Tests for `stalbot.application.services.posters.PosterService` (Часть IX, Э11).

The layout JSON files under `src/stalbot/assets/posters/` are real,
extracted from `СКУПКА.xlsx` (`scripts/extract_poster_assets.py`) — this
exercises `PosterService` against them directly rather than a synthetic
fixture, which is exactly what catches drift between the frozen layout
and the live catalog (item #1 of Часть IX's five-point test list): an
item present in the layout but renamed/removed from `catalog_items`
since extraction must be silently skipped, not crash the build.
"""

import re
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from stalbot.application.services.posters import PosterService, _assets_dir, _load_layout
from stalbot.domain.entities.catalog_item import CatalogItem
from stalbot.domain.enums import ItemCategory, PosterKind
from stalbot.domain.money import Rub
from stalbot.infrastructure.cache.repositories.catalog_items import CatalogItemsRepository
from stalbot.infrastructure.cache.repositories.items import normalize_item_name

_NOW = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)

#: Mirrors `scripts/extract_poster_assets.py` — the characters it replaces
#: in a filename, and the `_2`/`_3` suffix it appends on a name collision.
_UNSAFE_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*]')
_ICON_VARIANT_SUFFIX = re.compile(r"_\d+$")


async def _seed(
    items: CatalogItemsRepository, name: str, category: ItemCategory, *, price: int
) -> CatalogItem:
    name_norm = normalize_item_name(name)
    draft = CatalogItem(
        id=None,
        name=name,
        name_norm=name_norm,
        category=category,
        section=None,
        price_buy=Rub(price) if category is ItemCategory.RESOURCE else None,
        price_sell=Rub(price) if category is ItemCategory.BOOST else None,
        emoji=None,
        sort_order=0,
        shelter_item_id=None,
        created_at=_NOW,
        updated_at=None,
        deleted_at=None,
    )
    return await items.insert(draft)


async def test_build_includes_a_seeded_item_that_is_in_the_real_layout(
    connection: aiosqlite.Connection,
) -> None:
    """ "Уха" is a real name in `layout_boost_purchases.json` — seeding just it
    (not the whole real catalog) proves the live-price lookup path works
    without needing all 221 extracted items present in the test DB."""
    items = CatalogItemsRepository(connection)
    await _seed(items, "Уха", ItemCategory.RESOURCE, price=3000)
    service = PosterService(items)

    spec = await service.build(PosterKind.BOOST_PURCHASES)

    all_names = [slot.name for section in spec.sections for slot in section.slots]
    assert "Уха" in all_names
    slot = next(s for section in spec.sections for s in section.slots if s.name == "Уха")
    assert slot.price_text == "3 000 р."
    assert " " not in slot.price_text


async def test_build_skips_layout_items_missing_from_the_catalog(
    connection: aiosqlite.Connection,
) -> None:
    """An empty catalog must not crash the build — every layout item is
    simply absent from the result (empty-slot rule, Часть IX)."""
    items = CatalogItemsRepository(connection)
    service = PosterService(items)

    spec = await service.build(PosterKind.BOOST_PURCHASES)

    assert spec.sections == ()


async def test_build_uses_price_sell_for_boosts_and_price_buy_for_resources(
    connection: aiosqlite.Connection,
) -> None:
    items = CatalogItemsRepository(connection)
    await _seed(items, "Уха", ItemCategory.BOOST, price=6500)
    service = PosterService(items)

    spec = await service.build(PosterKind.BOOSTS)

    slot = next(s for section in spec.sections for s in section.slots if s.name == "Уха")
    assert slot.price_text == "6 500 р."


async def test_build_title_matches_kind(connection: aiosqlite.Connection) -> None:
    items = CatalogItemsRepository(connection)
    service = PosterService(items)

    spec = await service.build(PosterKind.BOOSTS)

    assert spec.title == "Продажа бустов"


async def test_build_boosts_layout_has_real_sections(connection: aiosqlite.Connection) -> None:
    """«Скрин бусты» is the one sheet with real section headers — an item
    seeded under one must come back grouped under its real section name."""
    items = CatalogItemsRepository(connection)
    await _seed(items, "Уха", ItemCategory.BOOST, price=6500)
    service = PosterService(items)

    spec = await service.build(PosterKind.BOOSTS)

    assert len(spec.sections) == 1
    assert spec.sections[0].name == "Кулинария"


async def test_build_resources_layout_has_no_sections(connection: aiosqlite.Connection) -> None:
    items = CatalogItemsRepository(connection)
    await _seed(items, "Металлолом", ItemCategory.RESOURCE, price=100)
    # "Металлолом" isn't a real item — proves the *shape* (no sections) on
    # an empty result rather than depending on a specific real name.
    service = PosterService(items)

    spec = await service.build(PosterKind.RESOURCES)

    assert all(section.name is None for section in spec.sections)


async def test_build_logo_path_points_at_a_real_file(connection: aiosqlite.Connection) -> None:
    items = CatalogItemsRepository(connection)
    service = PosterService(items)

    spec = await service.build(PosterKind.BOOSTS)

    assert spec.logo_path.is_file()


async def test_build_ignores_soft_deleted_items(connection: aiosqlite.Connection) -> None:
    items = CatalogItemsRepository(connection)
    item = await _seed(items, "Уха", ItemCategory.RESOURCE, price=3000)
    assert item.id is not None
    await items.soft_delete(item.id, now=_NOW)
    service = PosterService(items)

    spec = await service.build(PosterKind.BOOST_PURCHASES)

    all_names = [slot.name for section in spec.sections for slot in section.slots]
    assert "Уха" not in all_names


def test_every_layout_icon_belongs_to_its_own_item() -> None:
    """No slot may point at another item's picture (owner bug report: Морфин/лимонник).

    The extraction script (`scripts/extract_poster_assets.py`) dedupes
    icons by content hash across sheets, so two items whose anchors
    happened to yield identical bytes end up sharing one filename — which
    is how the «Скуп ваших бустов» sheet's Морфин came to render мякоть
    лимонника's picture. A filename is the only evidence the layout keeps
    of which item an icon was extracted for, so it has to match.
    """
    mismatched: list[str] = []
    for kind in PosterKind:
        layout = _load_layout(kind)
        for section in layout["sections"]:
            for entry in section["items"]:
                stem = _ICON_VARIANT_SUFFIX.sub("", Path(entry["icon"]).stem)
                if stem != _UNSAFE_FILENAME_CHARS.sub("_", entry["name_norm"]):
                    mismatched.append(f"{kind.value}: {entry['name']} -> {entry['icon']}")
    assert not mismatched


def test_every_layout_icon_file_exists() -> None:
    """A slot pointing at a missing file renders as a blank frame, silently."""
    missing: list[str] = []
    for kind in PosterKind:
        layout = _load_layout(kind)
        for section in layout["sections"]:
            for entry in section["items"]:
                if not (_assets_dir() / entry["icon"]).is_file():
                    missing.append(f"{kind.value}: {entry['name']} -> {entry['icon']}")
    assert not missing
