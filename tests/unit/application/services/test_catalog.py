"""Tests for `stalbot.application.services.catalog.CatalogService` (PLAN.md §7.5, §10.9).

`SheetsClient` is mocked; cache repositories are real, SQLite-backed, for
genuine round-trip confidence on the renumbering logic.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest
import pytest_asyncio

from stalbot.application.dto.boost_order_line import BoostOrderLine
from stalbot.application.services.catalog import CatalogService
from stalbot.domain.entities.item import Item
from stalbot.domain.enums import ItemCategory
from stalbot.domain.errors import DuplicateItemError, ItemNotFoundError
from stalbot.infrastructure.cache.db import CacheDb
from stalbot.infrastructure.cache.repositories.boost_order_lines import BoostOrderLinesRepository
from stalbot.infrastructure.cache.repositories.items import ItemsCacheRepository
from stalbot.infrastructure.sheets.client import SheetsClient


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


def _fake_sheets(*, block_rows: list[list[object]] | None = None) -> MagicMock:
    client = MagicMock(spec=SheetsClient)
    client.batch_get = AsyncMock(return_value={"DataBase!AA3:AG": block_rows or []})
    client.write_verified = AsyncMock()
    client.batch_update = AsyncMock()
    return client


def _service(
    connection: aiosqlite.Connection, *, sheets: MagicMock, clock: _FixedClock
) -> tuple[CatalogService, BoostOrderLinesRepository]:
    boost_lines = BoostOrderLinesRepository(connection)
    service = CatalogService(sheets, ItemsCacheRepository(connection), boost_lines, clock=clock)
    return service, boost_lines


def _row(
    item_id: int, name: str, category: str = "resource", *, buy: object = "", sell: object = ""
) -> list[object]:
    return [item_id, name, category, buy, sell, "", ""]


async def test_add_item_writes_row_and_upserts_cache(connection: aiosqlite.Connection) -> None:
    sheets = _fake_sheets()
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service, _lines = _service(connection, sheets=sheets, clock=clock)

    item = await service.add_item(
        name="Кристалл",
        category=ItemCategory.RESOURCE,
        price_buy=Decimal(120000),
        price_sell=None,
        emoji="crystal",
    )

    assert item.id == 1
    assert item.row == 3
    sheets.write_verified.assert_awaited_once()
    (data,), _ = sheets.write_verified.call_args
    row = data["DataBase!AA3:AG3"][0]
    assert row == [1, "Кристалл", "resource", 120000, "", "crystal", "02.08.2026 15:00"]

    items = ItemsCacheRepository(connection)
    cached = await items.get_by_id(1)
    assert cached is not None
    assert cached.name == "Кристалл"


async def test_add_item_computes_next_id_from_existing_catalog(
    connection: aiosqlite.Connection,
) -> None:
    items = ItemsCacheRepository(connection)
    await items.replace_all([_item(id=5, name="Топот", row=7)])
    sheets = _fake_sheets()
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service, _lines = _service(connection, sheets=sheets, clock=clock)

    item = await service.add_item(
        name="Кристалл", category=ItemCategory.RESOURCE, price_buy=None, price_sell=None, emoji=None
    )

    assert item.id == 6
    assert item.row == 8


async def test_add_item_rejects_duplicate_name_and_category(
    connection: aiosqlite.Connection,
) -> None:
    items = ItemsCacheRepository(connection)
    await items.replace_all([_item(id=1, name="Топот", category=ItemCategory.BOOST, row=3)])
    sheets = _fake_sheets()
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service, _lines = _service(connection, sheets=sheets, clock=clock)

    with pytest.raises(DuplicateItemError):
        await service.add_item(
            name="Топот",
            category=ItemCategory.BOOST,
            price_buy=None,
            price_sell=Decimal(1),
            emoji=None,
        )
    sheets.write_verified.assert_not_called()


async def test_delete_item_renumbers_and_clears_the_tail_row(
    connection: aiosqlite.Connection,
) -> None:
    block = [_row(1, "Топот"), _row(2, "Кристалл"), _row(3, "Хвост")]
    sheets = _fake_sheets(block_rows=block)
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service, _lines = _service(connection, sheets=sheets, clock=clock)

    result = await service.delete_item(2)

    assert result.deleted.name == "Кристалл"
    assert sheets.write_verified.await_count == 1
    (data,), _ = sheets.write_verified.call_args
    rewritten = data["DataBase!AA3:AG4"]
    assert [row[0] for row in rewritten] == [1, 2]  # Топот stays 1, Хвост renumbered 3 -> 2
    assert [row[1] for row in rewritten] == ["Топот", "Хвост"]

    sheets.batch_update.assert_awaited_once()
    (clear_data,), _ = sheets.batch_update.call_args
    assert clear_data == {"DataBase!AA5:AG5": [["", "", "", "", "", "", ""]]}

    items = ItemsCacheRepository(connection)
    remaining_ids = {item.id for item in await items.all()}
    assert remaining_ids == {1, 2}


async def test_delete_item_raises_when_id_not_found(connection: aiosqlite.Connection) -> None:
    sheets = _fake_sheets(block_rows=[_row(1, "Топот")])
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service, _lines = _service(connection, sheets=sheets, clock=clock)

    with pytest.raises(ItemNotFoundError):
        await service.delete_item(999)


async def test_delete_item_reassigns_boost_order_lines_of_renumbered_items(
    connection: aiosqlite.Connection,
) -> None:
    block = [
        _row(1, "Топот", "boost"),
        _row(2, "Кристалл", "resource"),
        _row(3, "Хвост", "resource"),
    ]
    sheets = _fake_sheets(block_rows=block)
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service, lines = _service(connection, sheets=sheets, clock=clock)
    await lines.upsert(
        BoostOrderLine(
            channel_id=555,
            item_id=3,
            item_name_norm="хвост",
            category=ItemCategory.RESOURCE,
            quantity=2,
        )
    )

    await service.delete_item(2)  # removes Кристалл; Хвост renumbers 3 -> 2

    remaining = await lines.list_for_channel(555)
    assert len(remaining) == 1
    assert remaining[0].item_id == 2


async def test_delete_item_removes_boost_order_lines_for_the_deleted_item(
    connection: aiosqlite.Connection,
) -> None:
    block = [_row(1, "Топот", "boost"), _row(2, "Кристалл", "resource")]
    sheets = _fake_sheets(block_rows=block)
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service, lines = _service(connection, sheets=sheets, clock=clock)
    await lines.upsert(
        BoostOrderLine(
            channel_id=555,
            item_id=2,
            item_name_norm="кристалл",
            category=ItemCategory.RESOURCE,
            quantity=1,
        )
    )

    result = await service.delete_item(2)

    assert result.affected_order_channels == [555]
    assert await lines.list_for_channel(555) == []


async def test_delete_item_saves_a_backup_snapshot_before_rewriting(
    connection: aiosqlite.Connection,
) -> None:
    block = [_row(1, "Топот"), _row(2, "Кристалл")]
    sheets = _fake_sheets(block_rows=block)
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service, _lines = _service(connection, sheets=sheets, clock=clock)

    await service.delete_item(2)

    cursor = await connection.execute(
        "SELECT value FROM sync_meta WHERE key = 'item_delete_backup'"
    )
    row = await cursor.fetchone()
    assert row is not None
    assert "Кристалл" in row["value"]


def _item(*, id: int, name: str, row: int, category: ItemCategory = ItemCategory.RESOURCE) -> Item:
    return Item(
        id=id,
        name=name,
        category=category,
        price_buy=None,
        price_sell=None,
        emoji=None,
        updated_at=None,
        row=row,
    )
