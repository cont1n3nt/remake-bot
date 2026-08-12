"""Tests for `stalbot.application.services.catalog.CatalogService` (PLAN.md §7.5, §10.9).

sqlite_migration.md §III.3, Э7: ids are DB-assigned surrogates, never
renumbered — `/del_item` soft-deletes (`deleted_at`), so there is no more
sheet block, no more renumbering, and no more "reassign this line's item id"
machinery to test. Cache repositories are real, SQLite-backed.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from stalbot.application.dto.boost_order_line import BoostOrderLine
from stalbot.application.services.catalog import CatalogService
from stalbot.domain.enums import ItemCategory
from stalbot.domain.errors import DuplicateItemError, InvalidCategoryPriceError, ItemNotFoundError
from stalbot.infrastructure.cache.db import CacheDb
from stalbot.infrastructure.cache.repositories.boost_order_lines import BoostOrderLinesRepository
from stalbot.infrastructure.cache.repositories.catalog_items import CatalogItemsRepository


@pytest_asyncio.fixture
async def connection(tmp_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    db = CacheDb(tmp_path / "cache.sqlite3")
    conn = await db.connect()
    yield conn
    await db.close()


class _FixedClock:
    def __init__(self, now: datetime) -> None:
        self.current = now

    def now(self) -> datetime:
        return self.current


def _service(
    connection: aiosqlite.Connection, *, clock: _FixedClock | None = None
) -> tuple[CatalogService, CatalogItemsRepository, BoostOrderLinesRepository]:
    items = CatalogItemsRepository(connection)
    lines = BoostOrderLinesRepository(connection)
    service = CatalogService(
        items, lines, clock=clock or _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    )
    return service, items, lines


async def test_add_item_assigns_an_id_and_persists(connection: aiosqlite.Connection) -> None:
    service, items, _lines = _service(connection)

    item = await service.add_item(
        name="Кристалл",
        category=ItemCategory.RESOURCE,
        price_buy=Decimal(120000),
        price_sell=None,
        emoji="crystal",
    )

    assert item.id is not None
    assert item.name == "Кристалл"
    assert item.price_buy == 120000
    cached = await items.get_by_id(item.id)
    assert cached is not None
    assert cached.name == "Кристалл"


async def test_add_item_rounds_fractional_price(connection: aiosqlite.Connection) -> None:
    """APP-2: a bare `int(...)` truncates toward zero — must round instead."""
    service, _items, _lines = _service(connection)

    item = await service.add_item(
        name="Кристалл",
        category=ItemCategory.RESOURCE,
        price_buy=Decimal("120000.5"),
        price_sell=None,
        emoji=None,
    )

    assert item.price_buy == 120001  # ROUND_HALF_UP, not truncation


async def test_add_item_two_items_get_different_ids(connection: aiosqlite.Connection) -> None:
    service, _items, _lines = _service(connection)

    first = await service.add_item(
        name="Кристалл", category=ItemCategory.RESOURCE, price_buy=None, price_sell=None, emoji=None
    )
    second = await service.add_item(
        name="Хвост", category=ItemCategory.RESOURCE, price_buy=None, price_sell=None, emoji=None
    )

    assert first.id != second.id


async def test_add_item_rejects_duplicate_name_and_category(
    connection: aiosqlite.Connection,
) -> None:
    service, _items, _lines = _service(connection)
    await service.add_item(
        name="Топот", category=ItemCategory.BOOST, price_buy=None, price_sell=Decimal(1), emoji=None
    )

    with pytest.raises(DuplicateItemError):
        await service.add_item(
            name="Топот",
            category=ItemCategory.BOOST,
            price_buy=None,
            price_sell=Decimal(2),
            emoji=None,
        )


async def test_add_item_allows_the_same_name_in_a_different_category(
    connection: aiosqlite.Connection,
) -> None:
    """§I.5: a handful of items (Уха, Морфин, Топот, Гром) legitimately exist
    as both a resource and a boost row."""
    service, _items, _lines = _service(connection)
    await service.add_item(
        name="Топот",
        category=ItemCategory.RESOURCE,
        price_buy=Decimal(1),
        price_sell=None,
        emoji=None,
    )

    boost = await service.add_item(
        name="Топот", category=ItemCategory.BOOST, price_buy=None, price_sell=Decimal(2), emoji=None
    )

    assert boost.category is ItemCategory.BOOST


async def test_add_item_rejects_a_resource_with_a_sell_price(
    connection: aiosqlite.Connection,
) -> None:
    """§I.5: category is the trade side — a resource is only ever bought."""
    service, _items, _lines = _service(connection)

    with pytest.raises(InvalidCategoryPriceError):
        await service.add_item(
            name="Кристалл",
            category=ItemCategory.RESOURCE,
            price_buy=None,
            price_sell=Decimal(1),
            emoji=None,
        )


async def test_add_item_rejects_a_boost_with_a_buy_price(connection: aiosqlite.Connection) -> None:
    service, _items, _lines = _service(connection)

    with pytest.raises(InvalidCategoryPriceError):
        await service.add_item(
            name="Топот",
            category=ItemCategory.BOOST,
            price_buy=Decimal(1),
            price_sell=None,
            emoji=None,
        )


async def test_delete_item_soft_deletes(connection: aiosqlite.Connection) -> None:
    service, items, _lines = _service(connection)
    item = await service.add_item(
        name="Кристалл", category=ItemCategory.RESOURCE, price_buy=None, price_sell=None, emoji=None
    )
    assert item.id is not None

    result = await service.delete_item(item.id)

    assert result.deleted.name == "Кристалл"
    assert await items.get_by_id(item.id) is not None  # still there, soft-deleted
    assert item.id not in {i.id for i in await items.all()}  # excluded from the active view


async def test_delete_item_does_not_touch_other_items(connection: aiosqlite.Connection) -> None:
    service, items, _lines = _service(connection)
    survivor = await service.add_item(
        name="Топот", category=ItemCategory.RESOURCE, price_buy=None, price_sell=None, emoji=None
    )
    doomed = await service.add_item(
        name="Кристалл", category=ItemCategory.RESOURCE, price_buy=None, price_sell=None, emoji=None
    )
    assert survivor.id is not None and doomed.id is not None

    await service.delete_item(doomed.id)

    # survivor's id is unchanged, never renumbered
    remaining = await items.all()
    assert {i.id for i in remaining} == {survivor.id}


async def test_delete_item_raises_when_id_not_found(connection: aiosqlite.Connection) -> None:
    service, _items, _lines = _service(connection)

    with pytest.raises(ItemNotFoundError):
        await service.delete_item(999)


async def test_delete_item_raises_when_already_deleted(connection: aiosqlite.Connection) -> None:
    service, _items, _lines = _service(connection)
    item = await service.add_item(
        name="Кристалл", category=ItemCategory.RESOURCE, price_buy=None, price_sell=None, emoji=None
    )
    assert item.id is not None
    await service.delete_item(item.id)

    with pytest.raises(ItemNotFoundError):
        await service.delete_item(item.id)


async def test_delete_item_removes_boost_order_lines_for_the_deleted_item(
    connection: aiosqlite.Connection,
) -> None:
    service, _items, lines = _service(connection)
    item = await service.add_item(
        name="Кристалл", category=ItemCategory.RESOURCE, price_buy=None, price_sell=None, emoji=None
    )
    assert item.id is not None
    await lines.upsert(
        BoostOrderLine(
            channel_id=555,
            item_id=item.id,
            item_name_norm=item.name_norm,
            category=item.category,
            quantity=1,
        )
    )

    result = await service.delete_item(item.id)

    assert result.affected_order_channels == [555]
    assert await lines.list_for_channel(555) == []


async def test_delete_item_leaves_other_channels_lines_alone(
    connection: aiosqlite.Connection,
) -> None:
    service, _items, lines = _service(connection)
    doomed = await service.add_item(
        name="Кристалл", category=ItemCategory.RESOURCE, price_buy=None, price_sell=None, emoji=None
    )
    survivor = await service.add_item(
        name="Топот", category=ItemCategory.RESOURCE, price_buy=None, price_sell=None, emoji=None
    )
    assert doomed.id is not None and survivor.id is not None
    await lines.upsert(
        BoostOrderLine(
            channel_id=555,
            item_id=survivor.id,
            item_name_norm=survivor.name_norm,
            category=survivor.category,
            quantity=1,
        )
    )

    await service.delete_item(doomed.id)

    remaining = await lines.list_for_channel(555)
    assert [line.item_id for line in remaining] == [survivor.id]
