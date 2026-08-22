"""Tests for `stalbot.application.services.boost_orders.BoostOrderService` (PLAN.md §11.6).

Both cache repositories are real, SQLite-backed, for genuine round-trip
confidence (same approach as `test_transaction_service.py`).

sqlite_migration.md Э7: catalog items are `CatalogItem`s with a DB-assigned
surrogate id now, not a hand-picked sheet-row-derived one — every test
inserts its fixture items and reads back the assigned ids rather than
hardcoding them.
"""

from datetime import UTC, datetime
from decimal import Decimal

import aiosqlite

from stalbot.application.dto.boost_order_line import BoostOrderLine
from stalbot.application.services.boost_orders import (
    MAX_ORDER_LINES,
    MAX_QUANTITY,
    MIN_QUANTITY,
    BoostOrderService,
)
from stalbot.domain.entities.catalog_item import CatalogItem
from stalbot.domain.enums import ItemCategory
from stalbot.domain.money import Rub
from stalbot.infrastructure.cache.repositories.boost_order_lines import BoostOrderLinesRepository
from stalbot.infrastructure.cache.repositories.catalog_items import CatalogItemsRepository

_NOW = datetime(2026, 7, 31, 21, 45, tzinfo=UTC)


def _draft(
    name: str,
    *,
    price_sell: Decimal | None = None,
    price_buy: Decimal | None = None,
    category: ItemCategory,
) -> CatalogItem:
    return CatalogItem(
        id=None,
        name=name,
        name_norm=name.lower(),
        category=category,
        section=None,
        price_buy=Rub(int(price_buy)) if price_buy is not None else None,
        price_sell=Rub(int(price_sell)) if price_sell is not None else None,
        emoji=None,
        sort_order=0,
        shelter_item_id=None,
        created_at=_NOW,
        updated_at=None,
        deleted_at=None,
    )


async def _service_with_items(
    connection: aiosqlite.Connection, drafts: list[CatalogItem]
) -> tuple[BoostOrderService, list[CatalogItem]]:
    items_repo = CatalogItemsRepository(connection)
    persisted = [await items_repo.insert(draft) for draft in drafts]
    return BoostOrderService(BoostOrderLinesRepository(connection), items_repo), persisted


def _ids(items: list[CatalogItem]) -> frozenset[int]:
    return frozenset(item.id for item in items if item.id is not None)


def _boost_a() -> CatalogItem:
    return _draft("Топот", price_sell=Decimal(300000), category=ItemCategory.BOOST)


def _boost_b() -> CatalogItem:
    return _draft("Ускорение", price_sell=Decimal(150000), category=ItemCategory.BOOST)


def _resource() -> CatalogItem:
    return _draft("Кристалл", price_sell=None, category=ItemCategory.RESOURCE)


def _resource_with_price(name: str, price_buy: Decimal) -> CatalogItem:
    return _draft(name, price_buy=price_buy, category=ItemCategory.RESOURCE)


def _resource_for_sale(name: str, price_sell: Decimal) -> CatalogItem:
    return _draft(name, price_sell=price_sell, category=ItemCategory.RESOURCE)


async def test_list_available_items_includes_both_categories_with_a_sell_price(
    connection: aiosqlite.Connection,
) -> None:
    """The order picker isn't boosts-only — the seller also orders resources through it."""
    service, (boost_a, boost_b, no_sell_price, sellable_resource) = await _service_with_items(
        connection,
        [_boost_a(), _boost_b(), _resource(), _resource_for_sale("Аптечка", Decimal(50000))],
    )

    available = await service.list_available_items()

    assert {item.id for item in available} == {boost_a.id, boost_b.id, sellable_resource.id}
    assert no_sell_price.id not in {item.id for item in available}


async def test_apply_page_selection_adds_newly_checked_items(
    connection: aiosqlite.Connection,
) -> None:
    service, (boost_a, boost_b) = await _service_with_items(connection, [_boost_a(), _boost_b()])
    assert boost_a.id is not None

    await service.apply_page_selection(111, [boost_a, boost_b], frozenset({boost_a.id}))

    lines = await service.list_lines(111)
    assert [line.item_id for line in lines] == [boost_a.id]
    assert lines[0].quantity == 1


async def test_apply_page_selection_removes_newly_unchecked_items(
    connection: aiosqlite.Connection,
) -> None:
    service, (boost_a, boost_b) = await _service_with_items(connection, [_boost_a(), _boost_b()])
    assert boost_a.id is not None and boost_b.id is not None
    await service.apply_page_selection(111, [boost_a, boost_b], frozenset({boost_a.id, boost_b.id}))

    await service.apply_page_selection(111, [boost_a, boost_b], frozenset({boost_a.id}))

    lines = await service.list_lines(111)
    assert [line.item_id for line in lines] == [boost_a.id]


async def test_apply_page_selection_reports_no_rejections_under_the_cap(
    connection: aiosqlite.Connection,
) -> None:
    service, (boost_a, boost_b) = await _service_with_items(connection, [_boost_a(), _boost_b()])
    assert boost_a.id is not None and boost_b.id is not None

    rejected = await service.apply_page_selection(
        111, [boost_a, boost_b], frozenset({boost_a.id, boost_b.id})
    )

    assert rejected == frozenset()


async def test_apply_page_selection_caps_the_draft_at_max_order_lines(
    connection: aiosqlite.Connection,
) -> None:
    """TICK-5: the editor renders one Select with one option per line — Discord hard-caps
    a Select at 25 options, so the draft itself must never grow past that."""
    drafts = [
        _draft(f"Boost {i}", price_sell=Decimal(1000), category=ItemCategory.BOOST)
        for i in range(MAX_ORDER_LINES + 1)
    ]
    service, items = await _service_with_items(connection, drafts)
    full_page, overflow_item = items[:MAX_ORDER_LINES], items[MAX_ORDER_LINES]
    assert overflow_item.id is not None
    await service.apply_page_selection(111, full_page, _ids(full_page))

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
    drafts = [
        _draft(f"Boost {i}", price_sell=Decimal(1000), category=ItemCategory.BOOST)
        for i in range(MAX_ORDER_LINES + 1)
    ]
    service, items = await _service_with_items(connection, drafts)
    full_page = items[1 : MAX_ORDER_LINES + 1]  # everything but the first item
    await service.apply_page_selection(111, full_page, _ids(full_page))
    swap_in, swap_out = items[0], items[1]  # not-yet-in-draft, already-in-draft
    assert swap_in.id is not None

    # Same page submit: swap_in newly checked, swap_out newly unchecked — a
    # page always reports every option's current state, so both appear here.
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
    service, (boost_a, boost_b) = await _service_with_items(connection, [_boost_a(), _boost_b()])
    assert boost_a.id is not None and boost_b.id is not None
    await service.apply_page_selection(111, [boost_a], frozenset({boost_a.id}))

    # A different page's submit, not mentioning boost_a at all.
    await service.apply_page_selection(111, [boost_b], frozenset({boost_b.id}))

    lines = await service.list_lines(111)
    assert {line.item_id for line in lines} == {boost_a.id, boost_b.id}


async def test_apply_page_selection_does_not_reset_an_existing_lines_quantity(
    connection: aiosqlite.Connection,
) -> None:
    service, (boost_a,) = await _service_with_items(connection, [_boost_a()])
    assert boost_a.id is not None
    await service.apply_page_selection(111, [boost_a], frozenset({boost_a.id}))
    await service.set_quantity(111, boost_a.id, 5)

    await service.apply_page_selection(111, [boost_a], frozenset({boost_a.id}))

    lines = await service.list_lines(111)
    assert lines[0].quantity == 5


async def test_set_quantity_updates_an_existing_line(connection: aiosqlite.Connection) -> None:
    service, (boost_a,) = await _service_with_items(connection, [_boost_a()])
    assert boost_a.id is not None
    await service.apply_page_selection(111, [boost_a], frozenset({boost_a.id}))

    await service.set_quantity(111, boost_a.id, 7)

    lines = await service.list_lines(111)
    assert lines[0].quantity == 7


async def test_set_quantity_is_a_no_op_for_a_missing_line(connection: aiosqlite.Connection) -> None:
    service, (boost_a,) = await _service_with_items(connection, [_boost_a()])
    assert boost_a.id is not None

    await service.set_quantity(111, boost_a.id, 7)  # must not raise

    assert await service.list_lines(111) == []


async def test_set_quantity_clamps_to_the_valid_range(connection: aiosqlite.Connection) -> None:
    """APP-7: unlike `adjust_quantity`, this had no clamp of its own — safe today only
    because the sole caller already validates, which isn't a guarantee this method makes."""
    service, (boost_a,) = await _service_with_items(connection, [_boost_a()])
    assert boost_a.id is not None
    await service.apply_page_selection(111, [boost_a], frozenset({boost_a.id}))

    await service.set_quantity(111, boost_a.id, MAX_QUANTITY + 1000)
    lines = await service.list_lines(111)
    assert lines[0].quantity == MAX_QUANTITY

    await service.set_quantity(111, boost_a.id, MIN_QUANTITY - 1000)
    lines = await service.list_lines(111)
    assert lines[0].quantity == MIN_QUANTITY


async def test_adjust_quantity_increments_and_decrements(connection: aiosqlite.Connection) -> None:
    service, (boost_a,) = await _service_with_items(connection, [_boost_a()])
    assert boost_a.id is not None
    await service.apply_page_selection(111, [boost_a], frozenset({boost_a.id}))

    after_plus = await service.adjust_quantity(111, boost_a.id, 1)
    after_minus = await service.adjust_quantity(111, boost_a.id, -1)

    assert after_plus == 2
    assert after_minus == 1


async def test_adjust_quantity_clamps_to_the_valid_range(connection: aiosqlite.Connection) -> None:
    service, (boost_a,) = await _service_with_items(connection, [_boost_a()])
    assert boost_a.id is not None
    await service.apply_page_selection(111, [boost_a], frozenset({boost_a.id}))

    at_minimum = await service.adjust_quantity(111, boost_a.id, -100)
    await service.set_quantity(111, boost_a.id, MAX_QUANTITY)
    at_maximum = await service.adjust_quantity(111, boost_a.id, 100)

    assert at_minimum == MIN_QUANTITY
    assert at_maximum == MAX_QUANTITY


async def test_adjust_quantity_returns_none_for_a_missing_line(
    connection: aiosqlite.Connection,
) -> None:
    service, (boost_a,) = await _service_with_items(connection, [_boost_a()])
    assert boost_a.id is not None

    assert await service.adjust_quantity(111, boost_a.id, 1) is None


async def test_remove_line_deletes_it(connection: aiosqlite.Connection) -> None:
    service, (boost_a,) = await _service_with_items(connection, [_boost_a()])
    assert boost_a.id is not None
    await service.apply_page_selection(111, [boost_a], frozenset({boost_a.id}))

    await service.remove_line(111, boost_a.id)

    assert await service.list_lines(111) == []


async def test_list_lines_with_items_pairs_lines_with_their_catalog_item(
    connection: aiosqlite.Connection,
) -> None:
    service, (boost_a,) = await _service_with_items(connection, [_boost_a()])
    assert boost_a.id is not None
    await service.apply_page_selection(111, [boost_a], frozenset({boost_a.id}))

    lines_with_items = await service.list_lines_with_items(111)

    assert len(lines_with_items) == 1
    line, item = lines_with_items[0]
    assert line.item_id == boost_a.id
    assert item is not None
    assert item.name == "Топот"


async def test_list_lines_with_items_reports_none_for_a_deleted_item(
    connection: aiosqlite.Connection,
) -> None:
    items_repo = CatalogItemsRepository(connection)
    lines_repo = BoostOrderLinesRepository(connection)
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
    service, (boost_a, boost_b) = await _service_with_items(connection, [_boost_a(), _boost_b()])
    assert boost_a.id is not None and boost_b.id is not None
    await service.apply_page_selection(111, [boost_a, boost_b], frozenset({boost_a.id, boost_b.id}))
    await service.set_quantity(111, boost_a.id, 3)

    total = await service.compute_total(111)

    assert total == Decimal(300000) * 3 + Decimal(150000)


async def test_compute_total_skips_items_without_a_sell_price(
    connection: aiosqlite.Connection,
) -> None:
    no_price = _draft("Без цены", price_sell=None, category=ItemCategory.BOOST)
    service, (no_price_item,) = await _service_with_items(connection, [no_price])
    assert no_price_item.id is not None
    await service.apply_page_selection(111, [no_price_item], frozenset({no_price_item.id}))

    total = await service.compute_total(111)

    assert total == Decimal(0)


async def test_compute_total_uses_price_buy_for_resources(
    connection: aiosqlite.Connection,
) -> None:
    """Э8: resources only ever have `price_buy` (§I.5) — the calculator must sum that side."""
    resource = _resource_with_price("Аптечка", Decimal(500))
    service, (resource_item,) = await _service_with_items(connection, [resource])
    assert resource_item.id is not None
    await service.apply_page_selection(111, [resource_item], frozenset({resource_item.id}))
    await service.set_quantity(111, resource_item.id, 4)

    total = await service.compute_total(111)

    assert total == Decimal(500) * 4


async def test_compute_order_total_always_uses_price_sell_even_for_resources(
    connection: aiosqlite.Connection,
) -> None:
    """The opposite direction from `compute_total`: a boost order sells *to* the player."""
    resource = _resource_for_sale("Аптечка", Decimal(500))
    boost = _boost_a()
    service, (resource_item, boost_item) = await _service_with_items(
        connection, [resource, boost]
    )
    assert resource_item.id is not None and boost_item.id is not None
    await service.apply_page_selection(
        111, [resource_item, boost_item], frozenset({resource_item.id, boost_item.id})
    )
    await service.set_quantity(111, resource_item.id, 4)

    total = await service.compute_order_total(111)

    assert total == Decimal(500) * 4 + Decimal(300000)


async def test_compute_order_total_skips_a_resource_with_no_sell_price(
    connection: aiosqlite.Connection,
) -> None:
    resource = _resource_with_price("Кристалл", Decimal(500))  # price_buy only
    service, (resource_item,) = await _service_with_items(connection, [resource])
    assert resource_item.id is not None
    await service.apply_page_selection(111, [resource_item], frozenset({resource_item.id}))

    total = await service.compute_order_total(111)

    assert total == Decimal(0)


async def test_clear_removes_every_line(connection: aiosqlite.Connection) -> None:
    service, (boost_a, boost_b) = await _service_with_items(connection, [_boost_a(), _boost_b()])
    assert boost_a.id is not None and boost_b.id is not None
    await service.apply_page_selection(111, [boost_a, boost_b], frozenset({boost_a.id, boost_b.id}))

    await service.clear(111)

    assert await service.list_lines(111) == []


async def test_apply_bulk_entry_resolves_matching_lines(
    connection: aiosqlite.Connection,
) -> None:
    resource = _resource_with_price("Аптечка проводника", Decimal(500))
    service, (resource_item,) = await _service_with_items(connection, [resource])

    report = await service.apply_bulk_entry(111, "Аптечка проводника x12")

    assert [(item.id, qty) for item, qty in report.applied] == [(resource_item.id, 12)]
    assert report.not_parsed == []
    assert report.not_found == []
    assert report.ambiguous == []
    lines = await service.list_lines(111)
    assert lines[0].quantity == 12


async def test_apply_bulk_entry_accepts_cyrillic_and_multiplication_sign_separators(
    connection: aiosqlite.Connection,
) -> None:
    resource = _resource_with_price("Кристалл", Decimal(100))
    service, _ = await _service_with_items(connection, [resource])

    report = await service.apply_bulk_entry(111, "Кристалл х3\nКристалл×3")

    # Same item twice: the second line wins (bulk paste sets outright, not additive).
    assert len(report.applied) == 2
    lines = await service.list_lines(111)
    assert len(lines) == 1
    assert lines[0].quantity == 3


async def test_apply_bulk_entry_is_case_insensitive_and_trims_whitespace(
    connection: aiosqlite.Connection,
) -> None:
    resource = _resource_with_price("Аптечка проводника", Decimal(500))
    service, (resource_item,) = await _service_with_items(connection, [resource])

    report = await service.apply_bulk_entry(111, "  АПТЕЧКА ПРОВОДНИКА   x  7  ")

    assert [(item.id, qty) for item, qty in report.applied] == [(resource_item.id, 7)]


async def test_apply_bulk_entry_reports_unparseable_lines(
    connection: aiosqlite.Connection,
) -> None:
    service, _ = await _service_with_items(connection, [])

    report = await service.apply_bulk_entry(111, "просто текст без количества\nx5")

    assert report.applied == []
    assert report.not_parsed == ["просто текст без количества", "x5"]


async def test_apply_bulk_entry_rejects_quantity_above_the_max(
    connection: aiosqlite.Connection,
) -> None:
    resource = _resource_with_price("Кристалл", Decimal(100))
    service, _ = await _service_with_items(connection, [resource])

    report = await service.apply_bulk_entry(111, f"Кристалл x{MAX_QUANTITY + 1}")

    assert report.applied == []
    assert report.not_parsed == [f"Кристалл x{MAX_QUANTITY + 1}"]


async def test_apply_bulk_entry_treats_quantity_zero_as_removal(
    connection: aiosqlite.Connection,
) -> None:
    """Э8: no Select exists to pick one line out of a 100+-line draft — re-pasting
    "Название x0" is how a single line gets deleted instead."""
    resource = _resource_with_price("Кристалл", Decimal(100))
    service, (resource_item,) = await _service_with_items(connection, [resource])
    await service.apply_bulk_entry(111, "Кристалл x5")

    report = await service.apply_bulk_entry(111, "Кристалл x0")

    assert report.applied == []
    assert [item.id for item in report.removed] == [resource_item.id]
    assert await service.list_lines(111) == []


async def test_apply_bulk_entry_removal_is_a_no_op_for_a_line_not_in_the_draft(
    connection: aiosqlite.Connection,
) -> None:
    resource = _resource_with_price("Кристалл", Decimal(100))
    service, (resource_item,) = await _service_with_items(connection, [resource])

    report = await service.apply_bulk_entry(111, "Кристалл x0")

    assert [item.id for item in report.removed] == [resource_item.id]
    assert await service.list_lines(111) == []


async def test_apply_bulk_entry_skips_blank_lines(connection: aiosqlite.Connection) -> None:
    resource = _resource_with_price("Кристалл", Decimal(100))
    service, (resource_item,) = await _service_with_items(connection, [resource])

    report = await service.apply_bulk_entry(111, "\n\nКристалл x2\n\n")

    assert [(item.id, qty) for item, qty in report.applied] == [(resource_item.id, 2)]
    assert report.not_parsed == []


async def test_apply_bulk_entry_reports_names_with_no_catalog_match(
    connection: aiosqlite.Connection,
) -> None:
    service, _ = await _service_with_items(connection, [])

    report = await service.apply_bulk_entry(111, "Несуществующий предмет x5")

    assert report.applied == []
    assert report.not_found == ["Несуществующий предмет x5"]


async def test_apply_bulk_entry_reports_names_matching_both_categories_as_ambiguous(
    connection: aiosqlite.Connection,
) -> None:
    """§I.5: 25 items (e.g. «Уха») exist as both a resource and a boost — bulk entry can't
    guess which one the owner meant, so it surfaces the line instead of picking one."""
    resource = _draft("Уха", price_buy=Decimal(3000), category=ItemCategory.RESOURCE)
    boost = _draft("Уха", price_sell=Decimal(6500), category=ItemCategory.BOOST)
    service, _ = await _service_with_items(connection, [resource, boost])

    report = await service.apply_bulk_entry(111, "Уха x2")

    assert report.applied == []
    assert report.ambiguous == ["Уха x2"]


async def test_apply_bulk_entry_does_not_enforce_max_order_lines(
    connection: aiosqlite.Connection,
) -> None:
    """Э8, §I.9: the calculator must hold 100+ lines — only the ticket's Select-based
    picker (`apply_page_selection`) is bounded by `MAX_ORDER_LINES`."""
    drafts = [
        _resource_with_price(f"Предмет {i}", Decimal(100)) for i in range(MAX_ORDER_LINES + 5)
    ]
    service, _ = await _service_with_items(connection, drafts)
    text = "\n".join(f"Предмет {i} x1" for i in range(MAX_ORDER_LINES + 5))

    report = await service.apply_bulk_entry(111, text)

    assert len(report.applied) == MAX_ORDER_LINES + 5
    assert len(await service.list_lines(111)) == MAX_ORDER_LINES + 5


async def test_apply_bulk_entry_does_not_apply_unresolved_lines_but_keeps_the_rest(
    connection: aiosqlite.Connection,
) -> None:
    resource = _resource_with_price("Кристалл", Decimal(100))
    service, (resource_item,) = await _service_with_items(connection, [resource])

    report = await service.apply_bulk_entry(111, "Кристалл x5\nНесуществующий x2\nсломанная строка")

    assert [(item.id, qty) for item, qty in report.applied] == [(resource_item.id, 5)]
    assert report.not_found == ["Несуществующий x2"]
    assert report.not_parsed == ["сломанная строка"]
