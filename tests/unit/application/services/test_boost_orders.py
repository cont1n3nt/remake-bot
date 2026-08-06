"""Tests for `stalbot.application.services.boost_orders.BoostOrderService` (PLAN.md §11.6).

Both cache repositories are real, SQLite-backed, for genuine round-trip
confidence (same approach as `test_transaction_service.py`).
"""

from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path

import aiosqlite
import pytest_asyncio

from stalbot.application.dto.boost_order_line import BoostOrderLine
from stalbot.application.services.boost_orders import (
    MAX_ORDER_LINES,
    MAX_QUANTITY,
    MIN_QUANTITY,
    BoostOrderService,
)
from stalbot.domain.entities.item import Item
from stalbot.domain.enums import ItemCategory
from stalbot.infrastructure.cache.db import CacheDb
from stalbot.infrastructure.cache.repositories.boost_order_lines import BoostOrderLinesRepository
from stalbot.infrastructure.cache.repositories.items import ItemsCacheRepository


@pytest_asyncio.fixture
async def connection(tmp_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    db = CacheDb(tmp_path / "cache.sqlite3")
    conn = await db.connect()
    yield conn
    await db.close()


def _item(item_id: int, name: str, *, price_sell: Decimal | None, category: ItemCategory) -> Item:
    return Item(
        id=item_id,
        name=name,
        category=category,
        price_buy=None,
        price_sell=price_sell,
        emoji=None,
        updated_at=None,
        row=item_id + 2,
    )


async def _service(connection: aiosqlite.Connection, items: list[Item]) -> BoostOrderService:
    items_repo = ItemsCacheRepository(connection)
    await items_repo.replace_all(items)
    return BoostOrderService(BoostOrderLinesRepository(connection), items_repo)


_BOOST_A = _item(1, "Топот", price_sell=Decimal(300000), category=ItemCategory.BOOST)
_BOOST_B = _item(2, "Ускорение", price_sell=Decimal(150000), category=ItemCategory.BOOST)
_RESOURCE = _item(3, "Кристалл", price_sell=Decimal(1000), category=ItemCategory.RESOURCE)


async def test_list_available_boosts_filters_by_category(connection: aiosqlite.Connection) -> None:
    service = await _service(connection, [_BOOST_A, _BOOST_B, _RESOURCE])

    boosts = await service.list_available_boosts()

    assert {item.id for item in boosts} == {1, 2}


async def test_apply_page_selection_adds_newly_checked_items(
    connection: aiosqlite.Connection,
) -> None:
    service = await _service(connection, [_BOOST_A, _BOOST_B])

    await service.apply_page_selection(111, [_BOOST_A, _BOOST_B], frozenset({1}))

    lines = await service.list_lines(111)
    assert [line.item_id for line in lines] == [1]
    assert lines[0].quantity == 1


async def test_apply_page_selection_removes_newly_unchecked_items(
    connection: aiosqlite.Connection,
) -> None:
    service = await _service(connection, [_BOOST_A, _BOOST_B])
    await service.apply_page_selection(111, [_BOOST_A, _BOOST_B], frozenset({1, 2}))

    await service.apply_page_selection(111, [_BOOST_A, _BOOST_B], frozenset({1}))

    lines = await service.list_lines(111)
    assert [line.item_id for line in lines] == [1]


async def test_apply_page_selection_reports_no_rejections_under_the_cap(
    connection: aiosqlite.Connection,
) -> None:
    service = await _service(connection, [_BOOST_A, _BOOST_B])

    rejected = await service.apply_page_selection(111, [_BOOST_A, _BOOST_B], frozenset({1, 2}))

    assert rejected == frozenset()


async def test_apply_page_selection_caps_the_draft_at_max_order_lines(
    connection: aiosqlite.Connection,
) -> None:
    """TICK-5: the editor renders one Select with one option per line — Discord hard-caps
    a Select at 25 options, so the draft itself must never grow past that."""
    items = [
        _item(i, f"Boost {i}", price_sell=Decimal(1000), category=ItemCategory.BOOST)
        for i in range(1, MAX_ORDER_LINES + 2)
    ]
    service = await _service(connection, items)
    full_page, overflow_item = items[:MAX_ORDER_LINES], items[MAX_ORDER_LINES]
    await service.apply_page_selection(111, full_page, frozenset(item.id for item in full_page))

    rejected = await service.apply_page_selection(
        111, [overflow_item], frozenset({overflow_item.id})
    )

    assert rejected == frozenset({overflow_item.id})
    lines = await service.list_lines(111)
    assert len(lines) == MAX_ORDER_LINES
    assert overflow_item.id not in {line.item_id for line in lines}


async def test_apply_page_selection_lets_a_same_page_swap_through_at_the_cap(
    connection: aiosqlite.Connection,
) -> None:
    """A page can uncheck one item and check another in the same submission — the freed
    slot must count toward the cap immediately, regardless of catalog id order."""
    items = [
        _item(i, f"Boost {i}", price_sell=Decimal(1000), category=ItemCategory.BOOST)
        for i in range(1, MAX_ORDER_LINES + 2)
    ]
    service = await _service(connection, items)
    full_page = items[1 : MAX_ORDER_LINES + 1]  # ids 2..26, filling the draft to the cap
    await service.apply_page_selection(111, full_page, frozenset(item.id for item in full_page))
    swap_in, swap_out = items[0], items[1]  # id 1 (not in the draft), id 2 (in the draft)

    # Same page submit: id 1 newly checked, id 2 newly unchecked — a page
    # always reports every option's current state, so both appear here.
    rejected = await service.apply_page_selection(111, [swap_in, swap_out], frozenset({swap_in.id}))

    assert rejected == frozenset()
    lines = await service.list_lines(111)
    line_ids = {line.item_id for line in lines}
    assert len(lines) == MAX_ORDER_LINES
    assert swap_in.id in line_ids
    assert swap_out.id not in line_ids


async def test_apply_page_selection_leaves_other_pages_alone(
    connection: aiosqlite.Connection,
) -> None:
    service = await _service(connection, [_BOOST_A, _BOOST_B])
    await service.apply_page_selection(111, [_BOOST_A], frozenset({1}))

    # A different page's submit, not mentioning item 1 at all.
    await service.apply_page_selection(111, [_BOOST_B], frozenset({2}))

    lines = await service.list_lines(111)
    assert {line.item_id for line in lines} == {1, 2}


async def test_apply_page_selection_does_not_reset_an_existing_lines_quantity(
    connection: aiosqlite.Connection,
) -> None:
    service = await _service(connection, [_BOOST_A])
    await service.apply_page_selection(111, [_BOOST_A], frozenset({1}))
    await service.set_quantity(111, 1, 5)

    await service.apply_page_selection(111, [_BOOST_A], frozenset({1}))

    lines = await service.list_lines(111)
    assert lines[0].quantity == 5


async def test_set_quantity_updates_an_existing_line(connection: aiosqlite.Connection) -> None:
    service = await _service(connection, [_BOOST_A])
    await service.apply_page_selection(111, [_BOOST_A], frozenset({1}))

    await service.set_quantity(111, 1, 7)

    lines = await service.list_lines(111)
    assert lines[0].quantity == 7


async def test_set_quantity_is_a_no_op_for_a_missing_line(connection: aiosqlite.Connection) -> None:
    service = await _service(connection, [_BOOST_A])

    await service.set_quantity(111, 1, 7)  # must not raise

    assert await service.list_lines(111) == []


async def test_set_quantity_clamps_to_the_valid_range(connection: aiosqlite.Connection) -> None:
    """APP-7: unlike `adjust_quantity`, this had no clamp of its own — safe today only
    because the sole caller already validates, which isn't a guarantee this method makes."""
    service = await _service(connection, [_BOOST_A])
    await service.apply_page_selection(111, [_BOOST_A], frozenset({1}))

    await service.set_quantity(111, 1, MAX_QUANTITY + 1000)
    lines = await service.list_lines(111)
    assert lines[0].quantity == MAX_QUANTITY

    await service.set_quantity(111, 1, MIN_QUANTITY - 1000)
    lines = await service.list_lines(111)
    assert lines[0].quantity == MIN_QUANTITY


async def test_adjust_quantity_increments_and_decrements(connection: aiosqlite.Connection) -> None:
    service = await _service(connection, [_BOOST_A])
    await service.apply_page_selection(111, [_BOOST_A], frozenset({1}))

    after_plus = await service.adjust_quantity(111, 1, 1)
    after_minus = await service.adjust_quantity(111, 1, -1)

    assert after_plus == 2
    assert after_minus == 1


async def test_adjust_quantity_clamps_to_the_valid_range(connection: aiosqlite.Connection) -> None:
    service = await _service(connection, [_BOOST_A])
    await service.apply_page_selection(111, [_BOOST_A], frozenset({1}))

    at_minimum = await service.adjust_quantity(111, 1, -100)
    await service.set_quantity(111, 1, MAX_QUANTITY)
    at_maximum = await service.adjust_quantity(111, 1, 100)

    assert at_minimum == MIN_QUANTITY
    assert at_maximum == MAX_QUANTITY


async def test_adjust_quantity_returns_none_for_a_missing_line(
    connection: aiosqlite.Connection,
) -> None:
    service = await _service(connection, [_BOOST_A])

    assert await service.adjust_quantity(111, 1, 1) is None


async def test_remove_line_deletes_it(connection: aiosqlite.Connection) -> None:
    service = await _service(connection, [_BOOST_A])
    await service.apply_page_selection(111, [_BOOST_A], frozenset({1}))

    await service.remove_line(111, 1)

    assert await service.list_lines(111) == []


async def test_list_lines_with_items_pairs_lines_with_their_catalog_item(
    connection: aiosqlite.Connection,
) -> None:
    service = await _service(connection, [_BOOST_A])
    await service.apply_page_selection(111, [_BOOST_A], frozenset({1}))

    lines_with_items = await service.list_lines_with_items(111)

    assert len(lines_with_items) == 1
    line, item = lines_with_items[0]
    assert line.item_id == 1
    assert item is not None
    assert item.name == "Топот"


async def test_list_lines_with_items_reports_none_for_a_deleted_item(
    connection: aiosqlite.Connection,
) -> None:
    items_repo = ItemsCacheRepository(connection)
    lines_repo = BoostOrderLinesRepository(connection)
    await items_repo.replace_all([])
    await lines_repo.upsert(
        BoostOrderLine(
            channel_id=111,
            item_id=99,
            item_name_norm="ghost",
            category=ItemCategory.BOOST,
            quantity=1,
        )
    )
    service = BoostOrderService(lines_repo, items_repo)

    lines_with_items = await service.list_lines_with_items(111)

    assert lines_with_items == [(lines_with_items[0][0], None)]


async def test_compute_total_sums_quantity_times_price(connection: aiosqlite.Connection) -> None:
    service = await _service(connection, [_BOOST_A, _BOOST_B])
    await service.apply_page_selection(111, [_BOOST_A, _BOOST_B], frozenset({1, 2}))
    await service.set_quantity(111, 1, 3)

    total = await service.compute_total(111)

    assert total == Decimal(300000) * 3 + Decimal(150000)


async def test_compute_total_skips_items_without_a_sell_price(
    connection: aiosqlite.Connection,
) -> None:
    no_price = _item(5, "Без цены", price_sell=None, category=ItemCategory.BOOST)
    service = await _service(connection, [no_price])
    await service.apply_page_selection(111, [no_price], frozenset({5}))

    total = await service.compute_total(111)

    assert total == Decimal(0)


async def test_clear_removes_every_line(connection: aiosqlite.Connection) -> None:
    service = await _service(connection, [_BOOST_A, _BOOST_B])
    await service.apply_page_selection(111, [_BOOST_A, _BOOST_B], frozenset({1, 2}))

    await service.clear(111)

    assert await service.list_lines(111) == []
