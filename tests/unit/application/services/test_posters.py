"""Tests for `stalbot.application.services.posters.PosterService` (Часть IX, Э11).

заявка 13.09.2026 п.6: the layout comes from `poster_sections`/`poster_slots`
now, so these seed the real repositories rather than leaning on the frozen
JSON. The JSON itself is still checked at the bottom — it is the import
script's source, and the icon-naming bug it once hid («Морфин» drawn with
мякоть лимонника's picture) is exactly the kind that survives a move.
"""

import re
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from stalbot.application.services.posters import PosterService
from stalbot.domain.entities.catalog_item import CatalogItem
from stalbot.domain.entities.poster_layout import PosterSectionRow, PosterSlotRow
from stalbot.domain.enums import ItemCategory, PosterKind
from stalbot.domain.money import Rub
from stalbot.infrastructure.cache.repositories.catalog_items import CatalogItemsRepository
from stalbot.infrastructure.cache.repositories.items import normalize_item_name
from stalbot.infrastructure.cache.repositories.poster_layout import PosterLayoutRepository

_NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
_ICONS = Path("/icons")
_LOGO = Path("/logo.png")

#: Mirrors `scripts/extract_poster_assets.py` — the characters it replaces
#: in a filename, and the `_2`/`_3` suffix it appends on a name collision.
_UNSAFE_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*]')
_ICON_VARIANT_SUFFIX = re.compile(r"_\d+$")


async def _seed_item(
    items: CatalogItemsRepository, name: str, category: ItemCategory, *, price: int | None
) -> CatalogItem:
    name_norm = normalize_item_name(name)
    draft = CatalogItem(
        id=None,
        name=name,
        name_norm=name_norm,
        category=category,
        section=None,
        price_buy=Rub(price) if price is not None and category is ItemCategory.RESOURCE else None,
        price_sell=Rub(price) if price is not None and category is ItemCategory.BOOST else None,
        emoji=None,
        sort_order=0,
        shelter_item_id=None,
        created_at=_NOW,
        updated_at=None,
        deleted_at=None,
    )
    return await items.insert(draft)


async def _seed_slot(
    layout: PosterLayoutRepository,
    kind: PosterKind,
    *,
    section_name: str | None,
    item: CatalogItem | None = None,
    name_norm: str = "",
    display_name: str = "",
    bind: bool = True,
) -> int:
    section = await layout.get_or_create_section(kind, section_name, now=_NOW)
    assert section.id is not None
    return await layout.add_slot(
        PosterSlotRow(
            id=None,
            section_id=section.id,
            sort_order=await layout.next_sort_order(section.id),
            catalog_item_id=item.id if (item is not None and bind) else None,
            display_name=display_name or (item.name if item else ""),
            name_norm=name_norm or (item.name_norm if item else ""),
            icon_file="icon.png",
        ),
        now=_NOW,
    )


def _service(connection: aiosqlite.Connection) -> PosterService:
    return PosterService(
        CatalogItemsRepository(connection),
        PosterLayoutRepository(connection),
        icons_dir=_ICONS,
        logo_path=_LOGO,
    )


async def test_build_renders_a_seeded_slot_with_its_live_price(
    connection: aiosqlite.Connection,
) -> None:
    items = CatalogItemsRepository(connection)
    layout = PosterLayoutRepository(connection)
    item = await _seed_item(items, "Уха", ItemCategory.BOOST, price=3000)
    await _seed_slot(layout, PosterKind.BOOSTS, section_name="Кулинария", item=item)

    spec = await _service(connection).build(PosterKind.BOOSTS)

    assert len(spec.sections) == 1
    assert spec.sections[0].name == "Кулинария"
    slot = spec.sections[0].slots[0]
    assert slot.name == item.name
    assert "3 000" in slot.price_text
    assert slot.icon_path == _ICONS / "icon.png"


async def test_build_skips_a_slot_whose_item_has_no_price(
    connection: aiosqlite.Connection,
) -> None:
    """The empty-slot rule (Часть IX) survives the move into the database."""
    items = CatalogItemsRepository(connection)
    layout = PosterLayoutRepository(connection)
    item = await _seed_item(items, "Уха", ItemCategory.BOOST, price=None)
    await _seed_slot(layout, PosterKind.BOOSTS, section_name="Кулинария", item=item)

    spec = await _service(connection).build(PosterKind.BOOSTS)

    assert spec.sections == ()


async def test_build_skips_a_slot_whose_item_was_soft_deleted(
    connection: aiosqlite.Connection,
) -> None:
    items = CatalogItemsRepository(connection)
    layout = PosterLayoutRepository(connection)
    item = await _seed_item(items, "Уха", ItemCategory.BOOST, price=3000)
    assert item.id is not None
    await _seed_slot(layout, PosterKind.BOOSTS, section_name="Кулинария", item=item)
    await items.soft_delete(item.id, now=_NOW)

    spec = await _service(connection).build(PosterKind.BOOSTS)

    assert spec.sections == ()


async def test_build_falls_back_to_the_name_when_a_slot_has_no_binding(
    connection: aiosqlite.Connection,
) -> None:
    """Imported slots carry no `catalog_item_id` — they still have to resolve."""
    items = CatalogItemsRepository(connection)
    layout = PosterLayoutRepository(connection)
    item = await _seed_item(items, "Уха", ItemCategory.BOOST, price=3000)
    await _seed_slot(layout, PosterKind.BOOSTS, section_name="Кулинария", item=item, bind=False)

    spec = await _service(connection).build(PosterKind.BOOSTS)

    assert spec.sections[0].slots[0].name == item.name


async def test_build_skips_a_slot_that_resolves_to_nothing(
    connection: aiosqlite.Connection,
) -> None:
    layout = PosterLayoutRepository(connection)
    await _seed_slot(
        layout,
        PosterKind.BOOSTS,
        section_name="Кулинария",
        name_norm="призрак",
        display_name="Призрак",
    )

    spec = await _service(connection).build(PosterKind.BOOSTS)

    assert spec.sections == ()


async def test_build_ignores_an_item_of_the_other_trade_side(
    connection: aiosqlite.Connection,
) -> None:
    """A boost poster prices `price_sell`; a resource has none by the §I.5 CHECK."""
    items = CatalogItemsRepository(connection)
    layout = PosterLayoutRepository(connection)
    item = await _seed_item(items, "Болт", ItemCategory.RESOURCE, price=50)
    await _seed_slot(layout, PosterKind.BOOSTS, section_name="Кулинария", item=item)

    spec = await _service(connection).build(PosterKind.BOOSTS)

    assert spec.sections == ()


async def test_build_keeps_section_order_and_columns(
    connection: aiosqlite.Connection,
) -> None:
    items = CatalogItemsRepository(connection)
    layout = PosterLayoutRepository(connection)
    first = await _seed_item(items, "Первый", ItemCategory.BOOST, price=1)
    second = await _seed_item(items, "Второй", ItemCategory.BOOST, price=2)
    section_a = await layout.get_or_create_section(PosterKind.BOOSTS, "А", now=_NOW)
    section_b = await layout.add_section(
        PosterSectionRow(id=None, poster_kind=PosterKind.BOOSTS, name="Б", sort_order=1, columns=2),
        now=_NOW,
    )
    assert section_a.id is not None
    for section_id, item in ((section_a.id, first), (section_b, second)):
        await layout.add_slot(
            PosterSlotRow(
                id=None,
                section_id=section_id,
                sort_order=0,
                catalog_item_id=item.id,
                display_name=item.name,
                name_norm=item.name_norm,
                icon_file="icon.png",
            ),
            now=_NOW,
        )

    spec = await _service(connection).build(PosterKind.BOOSTS)

    assert [section.name for section in spec.sections] == ["А", "Б"]
    assert spec.sections[1].columns == 2


async def test_build_keeps_slot_order_within_a_section(
    connection: aiosqlite.Connection,
) -> None:
    items = CatalogItemsRepository(connection)
    layout = PosterLayoutRepository(connection)
    for name in ("Первый", "Второй", "Третий"):
        item = await _seed_item(items, name, ItemCategory.BOOST, price=1)
        await _seed_slot(layout, PosterKind.BOOSTS, section_name="А", item=item)

    spec = await _service(connection).build(PosterKind.BOOSTS)

    assert [slot.name for slot in spec.sections[0].slots] == ["Первый", "Второй", "Третий"]


async def test_build_of_an_empty_layout_has_no_sections(
    connection: aiosqlite.Connection,
) -> None:
    spec = await _service(connection).build(PosterKind.RESOURCES)

    assert spec.sections == ()
    assert spec.logo_path == _LOGO


# -- the frozen JSON, still the import script's source ----------------------


def _assets_dir() -> Path:
    from importlib import resources

    return Path(str(resources.files("stalbot") / "assets" / "posters"))


def _layout_entries(kind: PosterKind) -> list[dict[str, str]]:
    """Every `{name, name_norm, icon}` entry of one layout file, flattened."""
    import json
    from typing import Any

    text = (_assets_dir() / f"layout_{kind.value}.json").read_text(encoding="utf-8")
    document: dict[str, list[dict[str, Any]]] = json.loads(text)
    return [entry for section in document["sections"] for entry in section["items"]]


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
        for entry in _layout_entries(kind):
            stem = _ICON_VARIANT_SUFFIX.sub("", Path(entry["icon"]).stem)
            if stem != _UNSAFE_FILENAME_CHARS.sub("_", entry["name_norm"]):
                mismatched.append(f"{kind.value}: {entry['name']} -> {entry['icon']}")
    assert not mismatched


def test_every_layout_icon_file_exists() -> None:
    """A slot pointing at a missing file renders as a blank frame, silently."""
    missing: list[str] = []
    for kind in PosterKind:
        for entry in _layout_entries(kind):
            if not (_assets_dir() / entry["icon"]).is_file():
                missing.append(f"{kind.value}: {entry['name']} -> {entry['icon']}")
    assert not missing
