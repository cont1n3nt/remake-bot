"""Tests for `BoostOrderLinesRepository` against a real (temp-file) SQLite connection."""

import aiosqlite
import pytest

from stalbot.application.dto.boost_order_line import BoostOrderLine
from stalbot.domain.enums import ItemCategory
from stalbot.infrastructure.cache.repositories.boost_order_lines import BoostOrderLinesRepository


def _line(channel_id: int, item_id: int, **overrides: object) -> BoostOrderLine:
    defaults: dict[str, object] = {
        "channel_id": channel_id,
        "item_id": item_id,
        "item_name_norm": "топот",
        "category": ItemCategory.BOOST,
        "quantity": 3,
    }
    defaults.update(overrides)
    return BoostOrderLine(**defaults)  # type: ignore[arg-type]


def test_boost_order_line_rejects_non_positive_quantity() -> None:
    with pytest.raises(ValueError, match="quantity must be positive"):
        _line(1, 1, quantity=0)


async def test_list_for_channel_empty_when_untracked(connection: aiosqlite.Connection) -> None:
    repo = BoostOrderLinesRepository(connection)
    assert await repo.list_for_channel(111) == []


async def test_upsert_then_list_round_trips(connection: aiosqlite.Connection) -> None:
    repo = BoostOrderLinesRepository(connection)
    await repo.upsert(_line(111, 1, quantity=3))
    await repo.upsert(_line(111, 2, item_name_norm="ускорение", quantity=1))

    lines = await repo.list_for_channel(111)

    assert [(line.item_id, line.quantity) for line in lines] == [(1, 3), (2, 1)]


async def test_upsert_updates_existing_line_quantity(connection: aiosqlite.Connection) -> None:
    repo = BoostOrderLinesRepository(connection)
    await repo.upsert(_line(111, 1, quantity=3))

    await repo.upsert(_line(111, 1, quantity=5))

    lines = await repo.list_for_channel(111)
    assert len(lines) == 1
    assert lines[0].quantity == 5


async def test_delete_line_removes_only_that_item(connection: aiosqlite.Connection) -> None:
    repo = BoostOrderLinesRepository(connection)
    await repo.upsert(_line(111, 1))
    await repo.upsert(_line(111, 2))

    await repo.delete_line(111, 1)

    lines = await repo.list_for_channel(111)
    assert [line.item_id for line in lines] == [2]


async def test_clear_channel_removes_every_line(connection: aiosqlite.Connection) -> None:
    repo = BoostOrderLinesRepository(connection)
    await repo.upsert(_line(111, 1))
    await repo.upsert(_line(111, 2))

    await repo.clear_channel(111)

    assert await repo.list_for_channel(111) == []


async def test_lines_are_scoped_to_channel(connection: aiosqlite.Connection) -> None:
    repo = BoostOrderLinesRepository(connection)
    await repo.upsert(_line(111, 1))
    await repo.upsert(_line(222, 1))

    await repo.clear_channel(111)

    assert await repo.list_for_channel(111) == []
    assert len(await repo.list_for_channel(222)) == 1
