"""Tests for `stalbot.application.services.catalog.CatalogService` (PLAN.md §7.5, §10.9).

`SheetsClient` is mocked; cache repositories are real, SQLite-backed, for
genuine round-trip confidence on the renumbering logic.
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest
import pytest_asyncio

from stalbot.application.dto.boost_order_line import BoostOrderLine
from stalbot.application.dto.ticket_session import TicketSession
from stalbot.application.services.catalog import CatalogService
from stalbot.domain.entities.item import Item
from stalbot.domain.enums import ItemCategory, TicketKind, TicketStatus
from stalbot.domain.errors import DuplicateItemError, ItemNotFoundError
from stalbot.infrastructure.cache.db import CacheDb
from stalbot.infrastructure.cache.repositories.boost_order_lines import BoostOrderLinesRepository
from stalbot.infrastructure.cache.repositories.items import ItemsCacheRepository
from stalbot.infrastructure.cache.repositories.ticket_sessions import TicketSessionsRepository
from stalbot.infrastructure.sheets.client import SheetsClient
from stalbot.infrastructure.sheets.ratelimit import ReentrantAsyncLock


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
    # Real lock, not a bare MagicMock: `add_item`/`delete_item` run their
    # whole body under `async with self._sheets.locked(...)` (INFRA1-6), and
    # a plain MagicMock doesn't support the async context manager protocol.
    lock = ReentrantAsyncLock()
    client.locked = MagicMock(return_value=lock)
    return client


def _service(
    connection: aiosqlite.Connection, *, sheets: MagicMock, clock: _FixedClock
) -> tuple[CatalogService, BoostOrderLinesRepository, TicketSessionsRepository]:
    boost_lines = BoostOrderLinesRepository(connection)
    ticket_sessions = TicketSessionsRepository(connection)
    service = CatalogService(
        sheets, ItemsCacheRepository(connection), boost_lines, ticket_sessions, clock=clock
    )
    return service, boost_lines, ticket_sessions


def _row(
    item_id: int, name: str, category: str = "resource", *, buy: object = "", sell: object = ""
) -> list[object]:
    return [item_id, name, category, buy, sell, "", ""]


async def test_add_item_writes_row_and_upserts_cache(connection: aiosqlite.Connection) -> None:
    sheets = _fake_sheets()
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service, _lines, _sessions = _service(connection, sheets=sheets, clock=clock)

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


async def test_add_item_rounds_fractional_price_consistently_for_sheet_and_cache(
    connection: aiosqlite.Connection,
) -> None:
    """APP-2: a bare `int(...)` truncated toward zero while the cache kept the raw Decimal."""
    sheets = _fake_sheets()
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service, _lines, _sessions = _service(connection, sheets=sheets, clock=clock)

    item = await service.add_item(
        name="Кристалл",
        category=ItemCategory.RESOURCE,
        price_buy=Decimal("120000.5"),
        price_sell=None,
        emoji="crystal",
    )

    assert item.price_buy == Decimal(120001)  # ROUND_HALF_UP, not truncation
    (data,), _ = sheets.write_verified.call_args
    row = data["DataBase!AA3:AG3"][0]
    assert row[3] == 120001

    items = ItemsCacheRepository(connection)
    cached = await items.get_by_id(1)
    assert cached is not None
    assert cached.price_buy == Decimal(120001)


async def test_add_item_computes_next_id_from_existing_catalog(
    connection: aiosqlite.Connection,
) -> None:
    """APP-5: id/row come from the live sheet block, like `delete_item` — not the
    (possibly stale) cache, or two concurrent `/item_add` calls could collide."""
    sheets = _fake_sheets(block_rows=[_row(5, "Топот")])
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service, _lines, _sessions = _service(connection, sheets=sheets, clock=clock)

    item = await service.add_item(
        name="Кристалл", category=ItemCategory.RESOURCE, price_buy=None, price_sell=None, emoji=None
    )

    assert item.id == 6
    assert item.row == 4  # single existing row at DATA_START_ROW(3) -> next is 4


async def test_add_item_ignores_a_stale_cache_and_uses_the_live_block(
    connection: aiosqlite.Connection,
) -> None:
    """The cache lags the live sheet (e.g. a concurrent add hasn't synced back yet) —
    id/row must still come from what's actually on the sheet, not the stale cache."""
    items = ItemsCacheRepository(connection)
    await items.replace_all([_item(id=1, name="Топот", row=3)])  # cache: only 1 item
    sheets = _fake_sheets(block_rows=[_row(1, "Топот"), _row(2, "Кристалл")])  # sheet: 2 items
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service, _lines, _sessions = _service(connection, sheets=sheets, clock=clock)

    item = await service.add_item(
        name="Хвост", category=ItemCategory.RESOURCE, price_buy=None, price_sell=None, emoji=None
    )

    assert item.id == 3  # not 2, which a stale-cache read would have collided on
    assert item.row == 5


async def test_add_item_serializes_concurrent_calls_so_ids_never_collide(
    connection: aiosqlite.Connection,
) -> None:
    """INFRA1-6: reading the live block (APP-5) narrows the race but does not
    close it on its own — two concurrent `/item_add` calls can still both
    read the block before either writes back. `SheetsClient.locked()` must
    serialize the whole read -> compute -> write sequence so the second
    call always sees the first call's write before computing its own id."""

    class _StatefulSheets:
        """Fake whose `batch_get` reflects whatever `write_verified` has
        written so far, and logs a timeline of read/write boundaries —
        realistic enough to prove not just that the ids never collide, but
        that the second call's read never *starts* before the first call's
        write has fully *finished* (i.e. the two are truly serialized, not
        just accidentally non-colliding)."""

        def __init__(self) -> None:
            self._rows: list[list[object]] = []
            self._lock = ReentrantAsyncLock()
            self.events: list[str] = []

        async def batch_get(self, _ranges: list[str]) -> dict[str, list[list[object]]]:
            self.events.append("read_start")
            await asyncio.sleep(0)  # yield control so an unserialized caller could interleave
            self.events.append("read_end")
            return {"DataBase!AA3:AG": list(self._rows)}

        async def write_verified(self, data: dict[str, list[list[object]]]) -> None:
            self.events.append("write_start")
            await asyncio.sleep(0)
            (rows,) = data.values()
            self._rows.extend(rows)
            self.events.append("write_end")

        async def batch_update(self, _data: dict[str, list[list[object]]]) -> None:
            return None

        def locked(self, _sheet: str) -> ReentrantAsyncLock:
            return self._lock

    sheets = _StatefulSheets()
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service, _lines, _sessions = _service(
        connection,
        sheets=sheets,  # type: ignore[arg-type]
        clock=clock,
    )

    first, second = await asyncio.gather(
        service.add_item(
            name="Кристалл",
            category=ItemCategory.RESOURCE,
            price_buy=None,
            price_sell=None,
            emoji=None,
        ),
        service.add_item(
            name="Хвост",
            category=ItemCategory.RESOURCE,
            price_buy=None,
            price_sell=None,
            emoji=None,
        ),
    )

    assert {first.id, second.id} == {1, 2}
    assert {first.row, second.row} == {3, 4}
    second_read_start = sheets.events.index("read_start", sheets.events.index("read_start") + 1)
    first_write_end = sheets.events.index("write_end")
    assert second_read_start > first_write_end, sheets.events


async def test_add_item_rejects_duplicate_name_and_category(
    connection: aiosqlite.Connection,
) -> None:
    sheets = _fake_sheets(block_rows=[_row(1, "Топот", "boost")])
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service, _lines, _sessions = _service(connection, sheets=sheets, clock=clock)

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
    service, _lines, _sessions = _service(connection, sheets=sheets, clock=clock)

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
    service, _lines, _sessions = _service(connection, sheets=sheets, clock=clock)

    with pytest.raises(ItemNotFoundError):
        await service.delete_item(999)


async def test_delete_item_serializes_concurrent_calls(connection: aiosqlite.Connection) -> None:
    """INFRA1-6: the read -> renumber -> write -> cache-reassignment sequence
    must be fully serialized, not just the sheet write itself — otherwise a
    second concurrent `/del_item` could run its own reassignment against an
    id-remapping snapshot the first call's write has already superseded.

    This must observe the *reassignment* step specifically (not just the
    sheet read/write) — an earlier version of this test only logged
    sheet-level events and kept passing even when the lock's `async with`
    block was narrowed back to just read+write, because the reassignment
    loop's SQLite calls weren't instrumented at all."""

    class _StatefulSheets:
        def __init__(self, rows: list[list[object]]) -> None:
            self._rows = rows
            self._lock = ReentrantAsyncLock()
            self.events: list[str] = []

        async def batch_get(self, _ranges: list[str]) -> dict[str, list[list[object]]]:
            self.events.append("read_start")
            await asyncio.sleep(0)  # yield control so an unserialized caller could interleave
            self.events.append("read_end")
            return {"DataBase!AA3:AG": list(self._rows)}

        async def write_verified(self, _data: dict[str, list[list[object]]]) -> None:
            self.events.append("write_start")
            await asyncio.sleep(0)
            self.events.append("write_end")

        async def batch_update(self, _data: dict[str, list[list[object]]]) -> None:
            return None

        def locked(self, _sheet: str) -> ReentrantAsyncLock:
            return self._lock

    class _EventLoggingTicketSessions:
        """Delegates to the real repository, but logs start/end around
        `clear_active_order_item_for` — the first thing `delete_item` does
        after its sheet write, i.e. exactly the step that used to sit
        outside the lock."""

        def __init__(self, real: TicketSessionsRepository, events: list[str]) -> None:
            self._real = real
            self._events = events

        async def clear_active_order_item_for(self, item_id: int) -> None:
            self._events.append("reassign_start")
            await asyncio.sleep(0)
            await self._real.clear_active_order_item_for(item_id)
            self._events.append("reassign_end")

        def __getattr__(self, name: str) -> object:
            return getattr(self._real, name)

    block = [_row(1, "Топот"), _row(2, "Кристалл"), _row(3, "Хвост")]
    sheets = _StatefulSheets(block)
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    real_sessions = TicketSessionsRepository(connection)
    logging_sessions = _EventLoggingTicketSessions(real_sessions, sheets.events)
    service = CatalogService(
        sheets,  # type: ignore[arg-type]
        ItemsCacheRepository(connection),
        BoostOrderLinesRepository(connection),
        logging_sessions,  # type: ignore[arg-type]
        clock=clock,
    )

    await asyncio.gather(service.delete_item(1), service.delete_item(3))

    events = sheets.events
    second_read_start = events.index("read_start", events.index("read_start") + 1)
    first_reassign_end = events.index("reassign_end")
    assert second_read_start > first_reassign_end, events


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
    service, lines, _sessions = _service(connection, sheets=sheets, clock=clock)
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
    service, lines, _sessions = _service(connection, sheets=sheets, clock=clock)
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


# --- APP-4: TicketSession.active_order_item_id must track /del_item's renumbering ---


async def test_delete_item_reassigns_the_active_order_item_of_renumbered_items(
    connection: aiosqlite.Connection,
) -> None:
    """A session with the renumbered item's OLD id selected must follow it to the new id,
    or its editor's +/-/delete buttons silently stop working until reselected (APP-4)."""
    block = [
        _row(1, "Топот", "boost"),
        _row(2, "Кристалл", "resource"),
        _row(3, "Хвост", "resource"),
    ]
    sheets = _fake_sheets(block_rows=block)
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service, _lines, sessions = _service(connection, sheets=sheets, clock=clock)
    await sessions.upsert(_ticket_session(channel_id=777, active_order_item_id=3))

    await service.delete_item(2)  # removes Кристалл; Хвост renumbers 3 -> 2

    session = await sessions.get(777)
    assert session is not None
    assert session.active_order_item_id == 2


async def test_delete_item_clears_the_active_order_item_of_the_deleted_item(
    connection: aiosqlite.Connection,
) -> None:
    """A session with the just-deleted item selected must be cleared, not left dangling."""
    block = [_row(1, "Топот", "boost"), _row(2, "Кристалл", "resource")]
    sheets = _fake_sheets(block_rows=block)
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service, _lines, sessions = _service(connection, sheets=sheets, clock=clock)
    await sessions.upsert(_ticket_session(channel_id=555, active_order_item_id=2))

    await service.delete_item(2)

    session = await sessions.get(555)
    assert session is not None
    assert session.active_order_item_id is None


async def test_delete_item_saves_a_backup_snapshot_before_rewriting(
    connection: aiosqlite.Connection,
) -> None:
    block = [_row(1, "Топот"), _row(2, "Кристалл")]
    sheets = _fake_sheets(block_rows=block)
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service, _lines, _sessions = _service(connection, sheets=sheets, clock=clock)

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


def _ticket_session(*, channel_id: int, active_order_item_id: int | None) -> TicketSession:
    now = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
    return TicketSession(
        channel_id=channel_id,
        kind=TicketKind.ORDER_BOOSTS,
        author_id=111,
        status=TicketStatus.FILLED,
        delivery_method=None,
        game_nick="Scaryyyyy",
        referrer_nick=None,
        referrer_discord_id=None,
        deadline=None,
        screenshot_url=None,
        screenshot_message_id=None,
        summary_message_id=None,
        panel_message_id=None,
        ocr_status="disabled",
        ocr_analysis_id=None,
        idempotency_key=None,
        created_at=now,
        updated_at=now,
        active_order_item_id=active_order_item_id,
    )
