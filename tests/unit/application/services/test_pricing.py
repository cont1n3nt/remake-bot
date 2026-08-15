"""Tests for `stalbot.application.services.pricing.PricingService` (PLAN.md §10.5-§10.8).

sqlite_migration.md Э7: prices live only in `catalog_items` now — no more
price sheets, no more `/sync_prices` (deleted along with them). Every write
also appends an `item_price_history` row, which these tests check directly
rather than asserting on `SheetsClient` calls. Cache repositories are real,
SQLite-backed.
"""

from datetime import UTC, datetime
from decimal import Decimal

import aiosqlite
import pytest

from stalbot.application.dto.price_change import PriceChange, group_price_changes
from stalbot.application.dto.price_import import PriceImportPlan
from stalbot.application.services.pricing import (
    PricingService,
    decode_price_list_bytes,
    render_price_list_txt,
)
from stalbot.domain.entities.catalog_item import CatalogItem
from stalbot.domain.enums import ItemCategory, PriceChangeSource, PriceField
from stalbot.domain.errors import ItemNotFoundError
from stalbot.domain.money import Rub, format_amount
from stalbot.infrastructure.cache.repositories.catalog_items import CatalogItemsRepository
from stalbot.infrastructure.cache.repositories.item_price_history import (
    ItemPriceHistoryRepository,
)
from tests.support.fake_clock import FakeClock

_NOW = datetime(2026, 7, 31, 21, 45, tzinfo=UTC)


def _service(
    connection: aiosqlite.Connection, *, clock: FakeClock | None = None
) -> tuple[PricingService, CatalogItemsRepository, ItemPriceHistoryRepository]:
    items = CatalogItemsRepository(connection)
    history = ItemPriceHistoryRepository(connection)
    service = PricingService(
        items, history, clock=clock or FakeClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    )
    return service, items, history


def _draft(**overrides: object) -> CatalogItem:
    name = str(overrides.get("name", "Хвост тушкана"))
    defaults: dict[str, object] = {
        "id": None,
        "name": name,
        "name_norm": name.lower(),
        "category": ItemCategory.RESOURCE,
        "section": None,
        "price_buy": Rub(18000),
        "price_sell": None,
        "emoji": "tail",
        "sort_order": 0,
        "shelter_item_id": None,
        "created_at": _NOW,
        "updated_at": None,
        "deleted_at": None,
    }
    defaults.update(overrides)
    return CatalogItem(**defaults)  # type: ignore[arg-type]


async def _seed(items: CatalogItemsRepository, **overrides: object) -> CatalogItem:
    return await items.insert(_draft(**overrides))


# --- set_price ---------------------------------------------------------


async def test_set_price_updates_the_catalog(connection: aiosqlite.Connection) -> None:
    service, items, _history = _service(connection)
    item = await _seed(items, price_buy=Rub(18000))
    assert item.id is not None

    change = await service.set_price(item.id, PriceField.BUY, Decimal(19500))

    assert change.old_price == 18000
    assert change.new_price == 19500
    updated = await items.get_by_id(item.id)
    assert updated is not None
    assert updated.price_buy == 19500


async def test_set_price_logs_history(connection: aiosqlite.Connection) -> None:
    service, items, history = _service(connection)
    item = await _seed(items, price_buy=Rub(18000))
    assert item.id is not None

    await service.set_price(item.id, PriceField.BUY, Decimal(19500), changed_by=42)

    (entry,) = await history.for_item(item.id)
    assert entry.field is PriceField.BUY
    assert entry.old_price == 18000
    assert entry.new_price == 19500
    assert entry.changed_by == 42
    assert entry.source is PriceChangeSource.SETPRICE


async def test_set_price_rounds_fractional_price(connection: aiosqlite.Connection) -> None:
    """APP-2: a bare `int(...)` truncates toward zero — must round instead."""
    service, items, _history = _service(connection)
    item = await _seed(items, price_buy=Rub(18000))
    assert item.id is not None

    change = await service.set_price(item.id, PriceField.BUY, Decimal("19500.5"))

    assert change.new_price == 19501  # ROUND_HALF_UP, not truncation


async def test_set_price_only_touches_the_given_field(connection: aiosqlite.Connection) -> None:
    service, items, _history = _service(connection)
    item = await _seed(items, category=ItemCategory.BOOST, price_buy=None, price_sell=Rub(300000))
    assert item.id is not None

    await service.set_price(item.id, PriceField.SELL, Decimal(310000))

    updated = await items.get_by_id(item.id)
    assert updated is not None
    assert updated.price_buy is None
    assert updated.price_sell == 310000


async def test_set_price_raises_when_item_missing(connection: aiosqlite.Connection) -> None:
    service, _items, _history = _service(connection)

    with pytest.raises(ItemNotFoundError):
        await service.set_price(999, PriceField.BUY, Decimal(1))


# --- TXT export / import round trip -------------------------------------


def test_render_price_list_txt_includes_header_and_rows() -> None:
    text = render_price_list_txt(
        [
            _draft(
                id=1,
                name="Топот",
                category=ItemCategory.BOOST,
                price_buy=None,
                price_sell=Rub(300000),
            )
        ],
        now=datetime(2026, 7, 31, 21, 45, tzinfo=UTC),
    )
    assert "# Прайс-лист Stalzone" in text
    assert "ID" in text and "Название" in text
    assert "1" in text
    assert format_amount(Decimal(300000), currency=False) in text


async def test_preview_import_finds_changes_by_id(connection: aiosqlite.Connection) -> None:
    service, items, _history = _service(connection)
    item = await _seed(
        items, name="Топот", category=ItemCategory.BOOST, price_buy=None, price_sell=Rub(300000)
    )
    assert item.id is not None
    text = f"{item.id} | Топот | boost |  | 310000 |  | \n"

    plan = await service.preview_import(text)

    assert plan.is_valid
    assert len(plan.changes) == 1
    assert plan.changes[0].field is PriceField.SELL
    assert plan.changes[0].new_price == Decimal(310000)


async def test_preview_import_falls_back_to_name_and_category_when_id_unknown(
    connection: aiosqlite.Connection,
) -> None:
    service, items, _history = _service(connection)
    item = await _seed(
        items, name="Топот", category=ItemCategory.BOOST, price_buy=None, price_sell=Rub(300000)
    )
    assert item.id is not None
    text = "999 | Топот | boost |  | 310000 |  | \n"

    plan = await service.preview_import(text)

    assert plan.is_valid
    assert plan.changes[0].item_id == item.id


async def test_preview_import_ignores_header_and_separator_and_comment_lines(
    connection: aiosqlite.Connection,
) -> None:
    service, _items, _history = _service(connection)
    text = (
        "# comment\n"
        "ID | Название | Категория | Скуп | Продажа | Эмодзи | Обновлено\n"
        "----+------+-----+-----+-----+-----+-----\n"
        "\n"
    )

    plan = await service.preview_import(text)

    assert plan == PriceImportPlan()


async def test_preview_import_rejects_negative_price(connection: aiosqlite.Connection) -> None:
    service, items, _history = _service(connection)
    item = await _seed(items)
    assert item.id is not None
    text = f"{item.id} | Хвост тушкана | resource | -100 |  |  | \n"

    plan = await service.preview_import(text)

    assert not plan.is_valid
    assert plan.changes == ()


async def test_preview_import_rejects_duplicate_id(connection: aiosqlite.Connection) -> None:
    service, items, _history = _service(connection)
    item = await _seed(items)
    assert item.id is not None
    text = (
        f"{item.id} | Хвост тушкана | resource | 18000 |  |  | \n"
        f"{item.id} | Хвост тушкана | resource | 19000 |  |  | \n"
    )

    plan = await service.preview_import(text)

    assert not plan.is_valid
    assert any("повторный ID" in issue.message for issue in plan.issues)


async def test_preview_import_rejects_unknown_id_and_missing_fallback(
    connection: aiosqlite.Connection,
) -> None:
    service, _items, _history = _service(connection)
    text = "42 | Призрак | resource | 1000 |  |  | \n"

    plan = await service.preview_import(text)

    assert not plan.is_valid
    assert "не найден в базе" in plan.issues[0].message


async def test_preview_import_reports_malformed_row(connection: aiosqlite.Connection) -> None:
    service, _items, _history = _service(connection)
    text = "not enough fields\n"

    plan = await service.preview_import(text)

    assert not plan.is_valid
    assert "полей" in plan.issues[0].message


async def test_preview_import_reports_bad_id(connection: aiosqlite.Connection) -> None:
    service, _items, _history = _service(connection)
    text = "abc | Топот | boost |  | 310000 |  | \n"

    plan = await service.preview_import(text)

    assert not plan.is_valid
    assert "ID" in plan.issues[0].message


async def test_preview_import_rounds_before_comparing_so_a_rounding_no_op_is_not_reported(
    connection: aiosqlite.Connection,
) -> None:
    """Mirrors set_price: comparing the raw parsed value against an already-rounded
    cached price would report a change for a line that rounds back to the same price.
    """
    service, items, _history = _service(connection)
    item = await _seed(items, price_buy=Rub(19501))
    assert item.id is not None
    text = f"{item.id} | Хвост тушкана | resource | 19500,6 |  |  | \n"

    plan = await service.preview_import(text)

    assert plan.changes == ()


async def test_preview_import_empty_field_clears_the_price(
    connection: aiosqlite.Connection,
) -> None:
    service, items, _history = _service(connection)
    item = await _seed(items, price_buy=Rub(18000))
    assert item.id is not None
    text = f"{item.id} | Хвост тушкана | resource |  |  |  | \n"

    plan = await service.preview_import(text)

    assert plan.is_valid
    assert plan.changes[0].new_price is None


async def test_apply_import_writes_every_changed_item(connection: aiosqlite.Connection) -> None:
    service, items, _history = _service(connection)
    item = await _seed(items, price_buy=Rub(18000))
    assert item.id is not None
    plan = await service.preview_import(f"{item.id} | Хвост тушкана | resource | 19500 |  |  | \n")

    await service.apply_import(plan, changed_by=42)

    updated = await items.get_by_id(item.id)
    assert updated is not None
    assert updated.price_buy == 19500


async def test_apply_import_logs_history_for_every_changed_field(
    connection: aiosqlite.Connection,
) -> None:
    service, items, history = _service(connection)
    item = await _seed(items, price_buy=Rub(18000))
    assert item.id is not None
    plan = await service.preview_import(f"{item.id} | Хвост тушкана | resource | 19500 |  |  | \n")

    await service.apply_import(plan, changed_by=42)

    (entry,) = await history.for_item(item.id)
    assert entry.old_price == 18000
    assert entry.new_price == 19500
    assert entry.changed_by == 42
    assert entry.source is PriceChangeSource.IMPORT


async def test_apply_import_rounds_fractional_price_consistently(
    connection: aiosqlite.Connection,
) -> None:
    service, items, _history = _service(connection)
    item = await _seed(items, price_buy=Rub(18000))
    assert item.id is not None
    plan = await service.preview_import(
        f"{item.id} | Хвост тушкана | resource | 19500,5 |  |  | \n"
    )

    await service.apply_import(plan)

    updated = await items.get_by_id(item.id)
    assert updated is not None
    assert updated.price_buy == 19501  # ROUND_HALF_UP, not truncation


async def test_apply_import_is_a_no_op_for_an_empty_plan(connection: aiosqlite.Connection) -> None:
    service, _items, history = _service(connection)

    await service.apply_import(PriceImportPlan())

    assert await history.for_item(1) == []


async def test_apply_import_skips_an_item_deleted_since_preview(
    connection: aiosqlite.Connection,
) -> None:
    service, items, _history = _service(connection)
    item = await _seed(items, price_buy=Rub(18000))
    assert item.id is not None
    plan = await service.preview_import(f"{item.id} | Хвост тушкана | resource | 19500 |  |  | \n")
    await items.soft_delete(item.id, now=_NOW)

    await service.apply_import(plan)  # must not raise

    still_gone = await items.get_by_id(item.id)
    assert still_gone is not None
    assert still_gone.deleted_at is not None


# --- decode_price_list_bytes ---------------------------------------------


def test_decode_price_list_bytes_prefers_utf8() -> None:
    assert decode_price_list_bytes("Топот".encode()) == "Топот"


def test_decode_price_list_bytes_strips_utf8_bom() -> None:
    assert decode_price_list_bytes("Топот".encode("utf-8-sig")) == "Топот"


def test_decode_price_list_bytes_falls_back_to_cp1251() -> None:
    assert decode_price_list_bytes("Топот".encode("cp1251")) == "Топот"


# --- group_price_changes ---------------------------------------------


def test_group_price_changes_splits_resource_boost_and_scalp() -> None:
    catalog = [
        _draft(id=1, name="Топот", category=ItemCategory.BOOST),
        _draft(id=2, name="Топот", category=ItemCategory.RESOURCE),
        _draft(id=3, name="Кристалл", category=ItemCategory.RESOURCE),
    ]
    changes = [
        _price_change(item_id=1, name="Топот", category=ItemCategory.BOOST),
        _price_change(item_id=2, name="Топот", category=ItemCategory.RESOURCE),
        _price_change(item_id=3, name="Кристалл", category=ItemCategory.RESOURCE),
    ]

    grouped = group_price_changes(changes, catalog)

    assert [c.item_id for c in grouped.boosts] == [1]
    assert [c.item_id for c in grouped.boost_scalp] == [2]
    assert [c.item_id for c in grouped.resources] == [3]


def _price_change(*, item_id: int, name: str, category: ItemCategory) -> PriceChange:
    return PriceChange(
        item_id=item_id,
        item_name=name,
        category=category,
        field=PriceField.BUY,
        old_price=None,
        new_price=Rub(1),
    )
