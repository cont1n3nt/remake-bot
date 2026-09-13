"""Tests for `application.services.poster_layout` (заявка 13.09.2026 п.6).

Real SQLite repositories; only the icon store is faked, since what it does
with bytes belongs to `tests/unit/infrastructure/posters/test_icon_store.py`.
What matters here is the layout staying consistent: a slot always has an
icon file, an icon file outlives no slot, and nothing lands on a poster it
does not belong on.
"""

from datetime import UTC, datetime

import aiosqlite
import pytest

from stalbot.application.services.poster_layout import (
    ItemAlreadyOnPosterError,
    PosterLayoutService,
    SlotNotFoundError,
    WrongPosterCategoryError,
)
from stalbot.domain.entities.catalog_item import CatalogItem
from stalbot.domain.enums import ItemCategory, PosterKind
from stalbot.domain.errors import ItemNotFoundError
from stalbot.domain.money import Rub
from stalbot.infrastructure.cache.repositories.catalog_items import CatalogItemsRepository
from stalbot.infrastructure.cache.repositories.items import normalize_item_name
from stalbot.infrastructure.cache.repositories.poster_layout import PosterLayoutRepository
from tests.support.fake_clock import FakeClock

_NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)

#: Bound once rather than inlined: a lone Cyrillic section name is a Latin
#: look-alike, and ruff flags the literal at every use site.
_SECTION_A = "А"  # noqa: RUF001
_SECTION_B = "Б"


class _FakeIconStore:
    """Remembers filenames instead of touching the disk."""

    def __init__(self) -> None:
        self.files: set[str] = set()
        self.saves = 0

    def save(self, data: bytes, *, name_norm: str) -> str:
        self.saves += 1
        filename = f"{name_norm}-{len(data)}-{self.saves}.png"
        self.files.add(filename)
        return filename

    def exists(self, filename: str) -> bool:
        return filename in self.files

    def delete_if_unused(self, filename: str, *, still_used: set[str]) -> bool:
        if filename in still_used or filename not in self.files:
            return False
        self.files.discard(filename)
        return True


async def _seed_item(
    items: CatalogItemsRepository,
    name: str,
    category: ItemCategory = ItemCategory.BOOST,
    *,
    price: int | None = 1000,
) -> CatalogItem:
    return await items.insert(
        CatalogItem(
            id=None,
            name=name,
            name_norm=normalize_item_name(name),
            category=category,
            section=None,
            price_buy=(
                Rub(price) if price is not None and category is ItemCategory.RESOURCE else None
            ),
            price_sell=(
                Rub(price) if price is not None and category is ItemCategory.BOOST else None
            ),
            emoji=None,
            sort_order=0,
            shelter_item_id=None,
            created_at=_NOW,
            updated_at=None,
            deleted_at=None,
        )
    )


def _service(
    connection: aiosqlite.Connection, icons: _FakeIconStore
) -> tuple[PosterLayoutService, PosterLayoutRepository, CatalogItemsRepository]:
    layout = PosterLayoutRepository(connection)
    items = CatalogItemsRepository(connection)
    service = PosterLayoutService(layout, items, icons, clock=FakeClock(_NOW))
    return service, layout, items


# -- add --------------------------------------------------------------------


async def test_add_item_creates_the_section_slot_and_icon(
    connection: aiosqlite.Connection,
) -> None:
    icons = _FakeIconStore()
    service, layout, items = _service(connection, icons)
    item = await _seed_item(items, "Топот")
    assert item.id is not None

    slot = await service.add_item(
        PosterKind.BOOSTS, item.id, section_name="Медицина", icon_data=b"png"
    )

    assert slot.display_name == "Топот"
    assert icons.exists(slot.icon_file)
    sections = await layout.sections(PosterKind.BOOSTS)
    assert [section.name for section in sections] == ["Медицина"]


async def test_add_item_reuses_an_existing_named_section(
    connection: aiosqlite.Connection,
) -> None:
    icons = _FakeIconStore()
    service, layout, items = _service(connection, icons)
    first = await _seed_item(items, "Топот")
    second = await _seed_item(items, "Удар")
    assert first.id is not None and second.id is not None

    await service.add_item(PosterKind.BOOSTS, first.id, section_name="Медицина", icon_data=b"a")
    await service.add_item(PosterKind.BOOSTS, second.id, section_name="Медицина", icon_data=b"b")

    assert len(await layout.sections(PosterKind.BOOSTS)) == 1


async def test_add_item_appends_after_the_existing_slots(
    connection: aiosqlite.Connection,
) -> None:
    icons = _FakeIconStore()
    service, _layout, items = _service(connection, icons)
    first = await _seed_item(items, "Топот")
    second = await _seed_item(items, "Удар")
    assert first.id is not None and second.id is not None

    await service.add_item(PosterKind.BOOSTS, first.id, section_name="Медицина", icon_data=b"a")
    slot = await service.add_item(
        PosterKind.BOOSTS, second.id, section_name="Медицина", icon_data=b"b"
    )

    assert slot.sort_order == 1


async def test_add_item_refuses_a_duplicate(connection: aiosqlite.Connection) -> None:
    icons = _FakeIconStore()
    service, _layout, items = _service(connection, icons)
    item = await _seed_item(items, "Топот")
    assert item.id is not None
    await service.add_item(PosterKind.BOOSTS, item.id, section_name="Медицина", icon_data=b"a")

    with pytest.raises(ItemAlreadyOnPosterError):
        await service.add_item(PosterKind.BOOSTS, item.id, section_name="Медицина", icon_data=b"b")


async def test_add_item_refuses_the_wrong_side_of_the_trade(
    connection: aiosqlite.Connection,
) -> None:
    """A boost poster prices what the bot sells; a resource is what it buys."""
    icons = _FakeIconStore()
    service, _layout, items = _service(connection, icons)
    item = await _seed_item(items, "Болт", ItemCategory.RESOURCE, price=50)
    assert item.id is not None

    with pytest.raises(WrongPosterCategoryError):
        await service.add_item(PosterKind.BOOSTS, item.id, section_name=None, icon_data=b"a")


async def test_add_item_refuses_an_unknown_item(connection: aiosqlite.Connection) -> None:
    service, _layout, _items = _service(connection, _FakeIconStore())

    with pytest.raises(ItemNotFoundError):
        await service.add_item(PosterKind.BOOSTS, 999, section_name=None, icon_data=b"a")


async def test_add_item_refuses_a_soft_deleted_item(connection: aiosqlite.Connection) -> None:
    icons = _FakeIconStore()
    service, _layout, items = _service(connection, icons)
    item = await _seed_item(items, "Топот")
    assert item.id is not None
    await items.soft_delete(item.id, now=_NOW)

    with pytest.raises(ItemNotFoundError):
        await service.add_item(PosterKind.BOOSTS, item.id, section_name=None, icon_data=b"a")


# -- icon -------------------------------------------------------------------


async def test_set_icon_swaps_the_file_and_drops_the_old_one(
    connection: aiosqlite.Connection,
) -> None:
    icons = _FakeIconStore()
    service, _layout, items = _service(connection, icons)
    item = await _seed_item(items, "Топот")
    assert item.id is not None
    original = await service.add_item(
        PosterKind.BOOSTS, item.id, section_name="Медицина", icon_data=b"a"
    )

    updated = await service.set_icon(PosterKind.BOOSTS, item.id, icon_data=b"bb")

    assert updated.icon_file != original.icon_file
    assert icons.exists(updated.icon_file)
    assert not icons.exists(original.icon_file)


async def test_set_icon_refuses_an_item_that_is_not_on_the_poster(
    connection: aiosqlite.Connection,
) -> None:
    service, _layout, items = _service(connection, _FakeIconStore())
    item = await _seed_item(items, "Топот")
    assert item.id is not None

    with pytest.raises(SlotNotFoundError):
        await service.set_icon(PosterKind.BOOSTS, item.id, icon_data=b"a")


# -- move -------------------------------------------------------------------


async def test_move_puts_the_slot_at_the_requested_position(
    connection: aiosqlite.Connection,
) -> None:
    icons = _FakeIconStore()
    service, layout, items = _service(connection, icons)
    ids = []
    for name in ("Первый", "Второй", "Третий"):
        item = await _seed_item(items, name)
        assert item.id is not None
        ids.append(item.id)
        await service.add_item(PosterKind.BOOSTS, item.id, section_name=_SECTION_A, icon_data=b"a")

    await service.move_item(PosterKind.BOOSTS, ids[2], section_name=None, position=1)

    section = (await layout.sections(PosterKind.BOOSTS))[0]
    assert section.id is not None
    order = [slot.display_name for slot in await layout.slots(section.id)]
    assert order == ["Третий", "Первый", "Второй"]


async def test_move_to_another_section(connection: aiosqlite.Connection) -> None:
    icons = _FakeIconStore()
    service, layout, items = _service(connection, icons)
    item = await _seed_item(items, "Топот")
    assert item.id is not None
    await service.add_item(PosterKind.BOOSTS, item.id, section_name=_SECTION_A, icon_data=b"a")

    await service.move_item(PosterKind.BOOSTS, item.id, section_name=_SECTION_B, position=None)

    sections = {section.name: section.id for section in await layout.sections(PosterKind.BOOSTS)}
    assert await layout.slots(sections[_SECTION_A] or 0) == []
    assert len(await layout.slots(sections[_SECTION_B] or 0)) == 1


async def test_move_clamps_a_position_past_the_end(connection: aiosqlite.Connection) -> None:
    icons = _FakeIconStore()
    service, _layout, items = _service(connection, icons)
    item = await _seed_item(items, "Топот")
    assert item.id is not None
    await service.add_item(PosterKind.BOOSTS, item.id, section_name=_SECTION_A, icon_data=b"a")

    slot = await service.move_item(PosterKind.BOOSTS, item.id, section_name=None, position=99)

    assert slot.sort_order == 0


# -- remove -----------------------------------------------------------------


async def test_remove_item_drops_the_slot_and_its_icon(
    connection: aiosqlite.Connection,
) -> None:
    icons = _FakeIconStore()
    service, layout, items = _service(connection, icons)
    item = await _seed_item(items, "Топот")
    assert item.id is not None
    slot = await service.add_item(
        PosterKind.BOOSTS, item.id, section_name=_SECTION_A, icon_data=b"a"
    )

    await service.remove_item(PosterKind.BOOSTS, item.id)

    assert await layout.find_slot(PosterKind.BOOSTS, item.id) is None
    assert not icons.exists(slot.icon_file)


async def test_remove_item_refuses_when_it_is_not_on_the_poster(
    connection: aiosqlite.Connection,
) -> None:
    service, _layout, items = _service(connection, _FakeIconStore())
    item = await _seed_item(items, "Топот")
    assert item.id is not None

    with pytest.raises(SlotNotFoundError):
        await service.remove_item(PosterKind.BOOSTS, item.id)


async def test_remove_every_slot_for_clears_all_posters(
    connection: aiosqlite.Connection,
) -> None:
    """`/del_item` path: a slot whose item is gone draws nothing at all."""
    icons = _FakeIconStore()
    service, layout, items = _service(connection, icons)
    item = await _seed_item(items, "Топот")
    assert item.id is not None
    await service.add_item(PosterKind.BOOSTS, item.id, section_name=_SECTION_A, icon_data=b"a")

    removed = await service.remove_every_slot_for(item.id)

    assert removed == 1
    assert await layout.slots_for_item(item.id) == []
    assert icons.files == set()


async def test_remove_every_slot_for_an_item_with_none_is_a_no_op(
    connection: aiosqlite.Connection,
) -> None:
    service, _layout, _items = _service(connection, _FakeIconStore())

    assert await service.remove_every_slot_for(999) == 0


# -- overview ---------------------------------------------------------------


async def test_overview_lists_slots_and_what_is_missing(
    connection: aiosqlite.Connection,
) -> None:
    icons = _FakeIconStore()
    service, _layout, items = _service(connection, icons)
    placed = await _seed_item(items, "Топот")
    await _seed_item(items, "Удар")
    assert placed.id is not None
    await service.add_item(PosterKind.BOOSTS, placed.id, section_name=_SECTION_A, icon_data=b"a")

    overview = await service.overview(PosterKind.BOOSTS)

    assert [entry.slot.display_name for entry in overview.entries] == ["Топот"]
    assert [item.name for item in overview.missing_from_poster] == ["Удар"]
    assert overview.entries[0].is_healthy


async def test_overview_flags_a_slot_whose_icon_file_vanished(
    connection: aiosqlite.Connection,
) -> None:
    icons = _FakeIconStore()
    service, _layout, items = _service(connection, icons)
    item = await _seed_item(items, "Топот")
    assert item.id is not None
    slot = await service.add_item(
        PosterKind.BOOSTS, item.id, section_name=_SECTION_A, icon_data=b"a"
    )
    icons.files.discard(slot.icon_file)

    overview = await service.overview(PosterKind.BOOSTS)

    assert overview.entries[0].icon_present is False
    assert not overview.entries[0].is_healthy


async def test_overview_omits_unpriced_items_from_the_missing_list(
    connection: aiosqlite.Connection,
) -> None:
    """An item with no price would be skipped by the empty-slot rule anyway."""
    service, _layout, items = _service(connection, _FakeIconStore())
    await _seed_item(items, "Топот", price=None)

    overview = await service.overview(PosterKind.BOOSTS)

    assert overview.missing_from_poster == ()
