"""Tests for `stalbot.application.services.temp_prices.TempPriceService` (заявка 21.08.2026 п.9)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import aiosqlite
import pytest

from stalbot.application.services.temp_prices import TempPriceService
from stalbot.domain.entities.catalog_item import CatalogItem
from stalbot.domain.enums import ItemCategory, PriceChangeSource, PriceField
from stalbot.domain.errors import ItemNotFoundError
from stalbot.domain.money import Rub
from stalbot.infrastructure.cache.repositories.catalog_items import CatalogItemsRepository
from stalbot.infrastructure.cache.repositories.item_price_history import (
    ItemPriceHistoryRepository,
)
from stalbot.infrastructure.cache.repositories.temp_prices import TempPricesRepository
from tests.support.fake_clock import FakeClock

_NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
_UNTIL = _NOW + timedelta(hours=3)
_AFTER_UNTIL = _UNTIL + timedelta(minutes=1)


def _item(**overrides: object) -> CatalogItem:
    defaults: dict[str, object] = {
        "id": None,
        "name": "Аптечка",
        "name_norm": "аптечка",
        "category": ItemCategory.RESOURCE,
        "section": None,
        "price_buy": Rub(300_000),
        "price_sell": None,
        "emoji": None,
        "sort_order": 0,
        "shelter_item_id": None,
        "created_at": _NOW,
        "updated_at": None,
        "deleted_at": None,
    }
    defaults.update(overrides)
    return CatalogItem(**defaults)  # type: ignore[arg-type]


async def _service(
    connection: aiosqlite.Connection, *, now: datetime = _NOW
) -> tuple[TempPriceService, CatalogItemsRepository]:
    items = CatalogItemsRepository(connection)
    history = ItemPriceHistoryRepository(connection)
    temp_prices = TempPricesRepository(connection)
    service = TempPriceService(temp_prices, items, history, clock=FakeClock(now))
    return service, items


async def test_set_temp_price_applies_immediately(connection: aiosqlite.Connection) -> None:
    service, items = await _service(connection)
    item = await items.insert(_item())
    assert item.id is not None

    change = await service.set_temp_price(
        item.id, PriceField.BUY, Decimal(500_000), _UNTIL, changed_by=42
    )

    assert change.old_price == 300_000
    assert change.new_price == 500_000
    updated = await items.get_by_id(item.id)
    assert updated is not None
    assert updated.price_buy == 500_000


async def test_revert_due_restores_the_original_price(connection: aiosqlite.Connection) -> None:
    setter, items = await _service(connection, now=_NOW)
    item = await items.insert(_item())
    assert item.id is not None
    await setter.set_temp_price(item.id, PriceField.BUY, Decimal(500_000), _UNTIL, changed_by=42)

    reverter, _items = await _service(connection, now=_AFTER_UNTIL)
    reverted = await reverter.revert_due()

    assert len(reverted) == 1
    assert reverted[0].old_price == 500_000
    assert reverted[0].new_price == 300_000
    updated = await items.get_by_id(item.id)
    assert updated is not None
    assert updated.price_buy == 300_000


async def test_revert_due_is_a_no_op_before_expiry(connection: aiosqlite.Connection) -> None:
    setter, items = await _service(connection, now=_NOW)
    item = await items.insert(_item())
    assert item.id is not None
    await setter.set_temp_price(item.id, PriceField.BUY, Decimal(500_000), _UNTIL, changed_by=42)

    still_early = await setter.revert_due()

    assert still_early == []
    updated = await items.get_by_id(item.id)
    assert updated is not None
    assert updated.price_buy == 500_000


async def test_reapplying_before_expiry_keeps_the_true_original(
    connection: aiosqlite.Connection,
) -> None:
    """A second `/temp_price` before the first reverts must not adopt the temp value as 'original'."""
    setter, items = await _service(connection, now=_NOW)
    item = await items.insert(_item())
    assert item.id is not None
    await setter.set_temp_price(item.id, PriceField.BUY, Decimal(500_000), _UNTIL, changed_by=42)
    await setter.set_temp_price(
        item.id, PriceField.BUY, Decimal(700_000), _UNTIL + timedelta(hours=1), changed_by=42
    )

    reverter, _items = await _service(connection, now=_UNTIL + timedelta(hours=2))
    reverted = await reverter.revert_due()

    assert len(reverted) == 1
    assert reverted[0].new_price == 300_000  # the true original, not 500_000


async def test_revert_due_logs_under_temp_price_source(connection: aiosqlite.Connection) -> None:
    setter, items = await _service(connection, now=_NOW)
    item = await items.insert(_item())
    assert item.id is not None
    await setter.set_temp_price(item.id, PriceField.BUY, Decimal(500_000), _UNTIL, changed_by=42)

    reverter, _items = await _service(connection, now=_AFTER_UNTIL)
    await reverter.revert_due()

    cursor = await connection.execute(
        "SELECT source FROM item_price_history WHERE item_id = ? ORDER BY id", (item.id,)
    )
    sources = [row["source"] async for row in cursor]
    assert sources == [PriceChangeSource.TEMP_PRICE.value, PriceChangeSource.TEMP_PRICE.value]


async def test_set_temp_price_rejects_an_unknown_item(connection: aiosqlite.Connection) -> None:
    service, _items = await _service(connection)

    with pytest.raises(ItemNotFoundError):
        await service.set_temp_price(999, PriceField.BUY, Decimal(1000), _UNTIL, changed_by=42)
