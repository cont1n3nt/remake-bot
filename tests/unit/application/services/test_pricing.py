"""Tests for `stalbot.application.services.pricing.PricingService` (PLAN.md §10.5-§10.8).

`SheetsClient` is mocked; the cache repository is real, SQLite-backed.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest
import pytest_asyncio

from stalbot.application.dto.price_change import PriceChange, group_price_changes
from stalbot.application.dto.price_import import PriceImportPlan
from stalbot.application.services.pricing import (
    PricingService,
    decode_price_list_bytes,
    render_price_list_txt,
)
from stalbot.domain.entities.item import Item
from stalbot.domain.enums import ItemCategory, PriceField
from stalbot.domain.errors import ItemNotFoundError
from stalbot.domain.money import format_amount
from stalbot.infrastructure.cache.db import CacheDb
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


def _fake_sheets(*, batch_get_result: dict[str, list[list[object]]] | None = None) -> MagicMock:
    client = MagicMock(spec=SheetsClient)
    client.batch_get = AsyncMock(return_value=batch_get_result or {})
    client.write_verified = AsyncMock()
    client.batch_update = AsyncMock()
    return client


def _service(
    connection: aiosqlite.Connection, *, sheets: MagicMock, clock: _FixedClock
) -> PricingService:
    return PricingService(sheets, ItemsCacheRepository(connection), clock=clock)


def _item(**overrides: object) -> Item:
    defaults: dict[str, object] = {
        "id": 1,
        "name": "Хвост тушкана",
        "category": ItemCategory.RESOURCE,
        "price_buy": Decimal(18000),
        "price_sell": None,
        "emoji": "tail",
        "updated_at": None,
        "row": 3,
    }
    defaults.update(overrides)
    return Item(**defaults)  # type: ignore[arg-type]


# --- set_price ---------------------------------------------------------


async def test_set_price_writes_the_cell_and_updates_cache(
    connection: aiosqlite.Connection,
) -> None:
    items = ItemsCacheRepository(connection)
    await items.replace_all([_item()])
    sheets = _fake_sheets()
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service = _service(connection, sheets=sheets, clock=clock)

    change = await service.set_price(1, PriceField.BUY, Decimal(19500))

    assert change.old_price == Decimal(18000)
    assert change.new_price == Decimal(19500)
    sheets.write_verified.assert_awaited_once_with(
        {
            "DataBase!AD3": [[19500]],
            "DataBase!AG3": [["02.08.2026 15:00"]],
        }
    )
    updated = await items.get_by_id(1)
    assert updated is not None
    assert updated.price_buy == Decimal(19500)


async def test_set_price_rounds_fractional_price_consistently_for_sheet_and_cache(
    connection: aiosqlite.Connection,
) -> None:
    """APP-2: a bare `int(...)` truncated toward zero while the cache kept the raw Decimal."""
    items = ItemsCacheRepository(connection)
    await items.replace_all([_item()])
    sheets = _fake_sheets()
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service = _service(connection, sheets=sheets, clock=clock)

    change = await service.set_price(1, PriceField.BUY, Decimal("19500.5"))

    assert change.new_price == Decimal(19501)  # ROUND_HALF_UP, not truncation
    sheets.write_verified.assert_awaited_once_with(
        {
            "DataBase!AD3": [[19501]],
            "DataBase!AG3": [["02.08.2026 15:00"]],
        }
    )
    updated = await items.get_by_id(1)
    assert updated is not None
    assert updated.price_buy == Decimal(19501)


async def test_set_price_raises_when_item_missing(connection: aiosqlite.Connection) -> None:
    sheets = _fake_sheets()
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service = _service(connection, sheets=sheets, clock=clock)

    with pytest.raises(ItemNotFoundError):
        await service.set_price(999, PriceField.BUY, Decimal(1))


# --- sync_prices ---------------------------------------------------------


async def test_sync_prices_updates_changed_cells_only(connection: aiosqlite.Connection) -> None:
    items = ItemsCacheRepository(connection)
    await items.replace_all(
        [
            _item(
                id=1, name="Топот", category=ItemCategory.RESOURCE, price_buy=Decimal(250000), row=5
            )
        ]
    )
    sheets = _fake_sheets(
        batch_get_result={
            "'Мейн скуп'!C1:C31": [["Топот"]],
            "'Мейн скуп'!D1:D31": [[260000]],
        }
    )
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service = _service(connection, sheets=sheets, clock=clock)

    report = await service.sync_prices()

    assert len(report.updated) == 1
    assert report.updated[0].old_price == Decimal(250000)
    assert report.updated[0].new_price == Decimal(260000)
    sheets.batch_update.assert_awaited_once()
    (data,), _ = sheets.batch_update.call_args
    assert data["DataBase!AD5"] == [[260000]]
    updated = await items.get_by_id(1)
    assert updated is not None
    assert updated.price_buy == Decimal(260000)


async def test_sync_prices_rounds_fractional_cell_consistently_for_sheet_and_cache(
    connection: aiosqlite.Connection,
) -> None:
    items = ItemsCacheRepository(connection)
    await items.replace_all(
        [
            _item(
                id=1, name="Топот", category=ItemCategory.RESOURCE, price_buy=Decimal(250000), row=5
            )
        ]
    )
    sheets = _fake_sheets(
        batch_get_result={
            "'Мейн скуп'!C1:C31": [["Топот"]],
            "'Мейн скуп'!D1:D31": [["260000.5"]],
        }
    )
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service = _service(connection, sheets=sheets, clock=clock)

    report = await service.sync_prices()

    assert report.updated[0].new_price == Decimal(260001)  # ROUND_HALF_UP, not truncation
    (data,), _ = sheets.batch_update.call_args
    assert data["DataBase!AD5"] == [[260001]]
    updated = await items.get_by_id(1)
    assert updated is not None
    assert updated.price_buy == Decimal(260001)


async def test_sync_prices_skips_unchanged_prices(connection: aiosqlite.Connection) -> None:
    items = ItemsCacheRepository(connection)
    await items.replace_all(
        [
            _item(
                id=1, name="Топот", category=ItemCategory.RESOURCE, price_buy=Decimal(250000), row=5
            )
        ]
    )
    sheets = _fake_sheets(
        batch_get_result={
            "'Мейн скуп'!C1:C31": [["Топот"]],
            "'Мейн скуп'!D1:D31": [[250000]],
        }
    )
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service = _service(connection, sheets=sheets, clock=clock)

    report = await service.sync_prices()

    assert report.updated == ()
    assert report.unchanged_count == 1
    sheets.batch_update.assert_not_called()


async def test_sync_prices_reports_names_not_found_in_the_catalog(
    connection: aiosqlite.Connection,
) -> None:
    sheets = _fake_sheets(
        batch_get_result={
            "'Мейн скуп'!C1:C31": [["Неизвестный предмет"]],
            "'Мейн скуп'!D1:D31": [[1000]],
        }
    )
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service = _service(connection, sheets=sheets, clock=clock)

    report = await service.sync_prices()

    assert report.not_found == ("Неизвестный предмет",)
    assert report.updated == ()


# --- TXT export / import round trip -------------------------------------


def test_render_price_list_txt_includes_header_and_rows() -> None:
    text = render_price_list_txt(
        [
            _item(
                id=1,
                name="Топот",
                category=ItemCategory.BOOST,
                price_buy=None,
                price_sell=Decimal(300000),
            )
        ],
        now=datetime(2026, 7, 31, 21, 45, tzinfo=UTC),
    )
    assert "# Прайс-лист Stalzone" in text
    assert "ID" in text and "Название" in text
    assert "1" in text
    assert format_amount(Decimal(300000), currency=False) in text


async def test_preview_import_finds_changes_by_id(connection: aiosqlite.Connection) -> None:
    items = ItemsCacheRepository(connection)
    await items.replace_all(
        [
            _item(
                id=1,
                name="Топот",
                category=ItemCategory.BOOST,
                price_buy=None,
                price_sell=Decimal(300000),
            )
        ]
    )
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service = _service(connection, sheets=_fake_sheets(), clock=clock)
    text = "1 | Топот | boost |  | 310000 |  | \n"

    plan = await service.preview_import(text)

    assert plan.is_valid
    assert len(plan.changes) == 1
    assert plan.changes[0].field is PriceField.SELL
    assert plan.changes[0].new_price == Decimal(310000)


async def test_preview_import_falls_back_to_name_and_category_when_id_unknown(
    connection: aiosqlite.Connection,
) -> None:
    items = ItemsCacheRepository(connection)
    await items.replace_all(
        [_item(id=1, name="Топот", category=ItemCategory.BOOST, price_sell=Decimal(300000))]
    )
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service = _service(connection, sheets=_fake_sheets(), clock=clock)
    text = "999 | Топот | boost |  | 310000 |  | \n"

    plan = await service.preview_import(text)

    assert plan.is_valid
    assert plan.changes[0].item_id == 1


async def test_preview_import_ignores_header_and_separator_and_comment_lines(
    connection: aiosqlite.Connection,
) -> None:
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service = _service(connection, sheets=_fake_sheets(), clock=clock)
    text = (
        "# comment\n"
        "ID | Название | Категория | Скуп | Продажа | Эмодзи | Обновлено\n"
        "----+------+-----+-----+-----+-----+-----\n"
        "\n"
    )

    plan = await service.preview_import(text)

    assert plan == PriceImportPlan()


async def test_preview_import_rejects_negative_price(connection: aiosqlite.Connection) -> None:
    items = ItemsCacheRepository(connection)
    await items.replace_all([_item(id=1)])
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service = _service(connection, sheets=_fake_sheets(), clock=clock)
    text = "1 | Хвост тушкана | resource | -100 |  |  | \n"

    plan = await service.preview_import(text)

    assert not plan.is_valid
    assert plan.changes == ()


async def test_preview_import_rejects_duplicate_id(connection: aiosqlite.Connection) -> None:
    items = ItemsCacheRepository(connection)
    await items.replace_all([_item(id=1)])
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service = _service(connection, sheets=_fake_sheets(), clock=clock)
    text = (
        "1 | Хвост тушкана | resource | 18000 |  |  | \n"
        "1 | Хвост тушкана | resource | 19000 |  |  | \n"
    )

    plan = await service.preview_import(text)

    assert not plan.is_valid
    assert any("повторный ID" in issue.message for issue in plan.issues)


async def test_preview_import_rejects_unknown_id_and_missing_fallback(
    connection: aiosqlite.Connection,
) -> None:
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service = _service(connection, sheets=_fake_sheets(), clock=clock)
    text = "42 | Призрак | resource | 1000 |  |  | \n"

    plan = await service.preview_import(text)

    assert not plan.is_valid
    assert "не найден в базе" in plan.issues[0].message


async def test_preview_import_reports_malformed_row(connection: aiosqlite.Connection) -> None:
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service = _service(connection, sheets=_fake_sheets(), clock=clock)
    text = "not enough fields\n"

    plan = await service.preview_import(text)

    assert not plan.is_valid
    assert "полей" in plan.issues[0].message


async def test_preview_import_reports_bad_id(connection: aiosqlite.Connection) -> None:
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service = _service(connection, sheets=_fake_sheets(), clock=clock)
    text = "abc | Топот | boost |  | 310000 |  | \n"

    plan = await service.preview_import(text)

    assert not plan.is_valid
    assert "ID" in plan.issues[0].message


async def test_preview_import_rounds_before_comparing_so_a_rounding_no_op_is_not_reported(
    connection: aiosqlite.Connection,
) -> None:
    """Mirrors sync_prices: comparing the raw parsed value against an already-rounded
    cached price would report a change for a line that rounds back to the same price.
    """
    items = ItemsCacheRepository(connection)
    await items.replace_all([_item(id=1, price_buy=Decimal(19501))])
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service = _service(connection, sheets=_fake_sheets(), clock=clock)
    text = "1 | Хвост тушкана | resource | 19500,6 |  |  | \n"

    plan = await service.preview_import(text)

    assert plan.changes == ()


async def test_preview_import_empty_field_clears_the_price(
    connection: aiosqlite.Connection,
) -> None:
    items = ItemsCacheRepository(connection)
    await items.replace_all([_item(id=1, price_buy=Decimal(18000))])
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service = _service(connection, sheets=_fake_sheets(), clock=clock)
    text = "1 | Хвост тушкана | resource |  |  |  | \n"

    plan = await service.preview_import(text)

    assert plan.is_valid
    assert plan.changes[0].new_price is None


async def test_apply_import_writes_every_changed_item(connection: aiosqlite.Connection) -> None:
    items = ItemsCacheRepository(connection)
    await items.replace_all([_item(id=1, price_buy=Decimal(18000), row=3)])
    sheets = _fake_sheets()
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service = _service(connection, sheets=sheets, clock=clock)
    plan = await service.preview_import("1 | Хвост тушкана | resource | 19500 |  |  | \n")

    await service.apply_import(plan)

    sheets.batch_update.assert_awaited_once()
    (data,), _ = sheets.batch_update.call_args
    assert data["DataBase!AD3"] == [[19500]]
    updated = await items.get_by_id(1)
    assert updated is not None
    assert updated.price_buy == Decimal(19500)


async def test_apply_import_rounds_fractional_price_consistently_for_sheet_and_cache(
    connection: aiosqlite.Connection,
) -> None:
    items = ItemsCacheRepository(connection)
    await items.replace_all([_item(id=1, price_buy=Decimal(18000), row=3)])
    sheets = _fake_sheets()
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service = _service(connection, sheets=sheets, clock=clock)
    plan = await service.preview_import("1 | Хвост тушкана | resource | 19500,5 |  |  | \n")

    await service.apply_import(plan)

    (data,), _ = sheets.batch_update.call_args
    assert data["DataBase!AD3"] == [[19501]]  # ROUND_HALF_UP, not truncation
    updated = await items.get_by_id(1)
    assert updated is not None
    assert updated.price_buy == Decimal(19501)


async def test_apply_import_is_a_no_op_for_an_empty_plan(connection: aiosqlite.Connection) -> None:
    sheets = _fake_sheets()
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service = _service(connection, sheets=sheets, clock=clock)

    await service.apply_import(PriceImportPlan())

    sheets.batch_update.assert_not_called()


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
        _item(id=1, name="Топот", category=ItemCategory.BOOST),
        _item(id=2, name="Топот", category=ItemCategory.RESOURCE),
        _item(id=3, name="Кристалл", category=ItemCategory.RESOURCE),
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
        new_price=Decimal(1),
    )
