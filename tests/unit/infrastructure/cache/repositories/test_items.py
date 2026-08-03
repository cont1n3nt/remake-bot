"""Tests for `ItemsCacheRepository` against a real (temp-file) SQLite connection."""

from datetime import UTC, datetime
from decimal import Decimal

import aiosqlite

from stalbot.domain.entities.item import Item
from stalbot.domain.enums import ItemCategory
from stalbot.infrastructure.cache.repositories.items import (
    ItemsCacheRepository,
    normalize_item_name,
)


def _item(item_id: int, name: str = "Хвост тушкана", **overrides: object) -> Item:
    defaults: dict[str, object] = {
        "id": item_id,
        "name": name,
        "category": ItemCategory.RESOURCE,
        "price_buy": Decimal(18000),
        "price_sell": None,
        "emoji": "tail",
        "updated_at": datetime(2026, 7, 29, 9, 10, tzinfo=UTC),
        "row": 3 + item_id,
    }
    defaults.update(overrides)
    return Item(**defaults)  # type: ignore[arg-type]


def test_normalize_item_name_collapses_whitespace_and_lowercases() -> None:
    assert normalize_item_name("  Хвост   Тушкана ") == "хвост тушкана"


async def test_replace_all_then_all_round_trips(connection: aiosqlite.Connection) -> None:
    repo = ItemsCacheRepository(connection)
    await repo.replace_all([_item(1), _item(2, name="Кристалл")])

    items = await repo.all()

    assert [item.id for item in items] == [1, 2]
    assert items[0].price_buy == Decimal(18000)
    assert items[0].updated_at == datetime(2026, 7, 29, 9, 10, tzinfo=UTC)


async def test_replace_all_clears_previous_contents(connection: aiosqlite.Connection) -> None:
    repo = ItemsCacheRepository(connection)
    await repo.replace_all([_item(1)])
    await repo.replace_all([_item(2, name="Кристалл")])

    items = await repo.all()

    assert [item.id for item in items] == [2]


async def test_by_category_filters(connection: aiosqlite.Connection) -> None:
    repo = ItemsCacheRepository(connection)
    await repo.replace_all(
        [
            _item(1, category=ItemCategory.RESOURCE),
            _item(2, name="Топот", category=ItemCategory.BOOST),
        ]
    )

    boosts = await repo.by_category(ItemCategory.BOOST)

    assert [item.id for item in boosts] == [2]


async def test_find_by_name_case_and_whitespace_insensitive(
    connection: aiosqlite.Connection,
) -> None:
    repo = ItemsCacheRepository(connection)
    await repo.replace_all([_item(1, name="Хвост тушкана")])

    found = await repo.find("  ХВОСТ   ТУШКАНА  ", None)

    assert found is not None
    assert found.id == 1


async def test_find_scoped_to_category_returns_none_when_absent(
    connection: aiosqlite.Connection,
) -> None:
    repo = ItemsCacheRepository(connection)
    await repo.replace_all([_item(1, category=ItemCategory.RESOURCE)])

    assert await repo.find("Хвост тушкана", ItemCategory.BOOST) is None


async def test_upsert_many_updates_existing_row(connection: aiosqlite.Connection) -> None:
    repo = ItemsCacheRepository(connection)
    await repo.replace_all([_item(1, price_buy=Decimal(1000))])

    await repo.upsert_many([_item(1, price_buy=Decimal(2000))])

    item = await repo.find("Хвост тушкана", ItemCategory.RESOURCE)
    assert item is not None
    assert item.price_buy == Decimal(2000)


async def test_item_with_no_prices_round_trips_as_none(connection: aiosqlite.Connection) -> None:
    repo = ItemsCacheRepository(connection)
    await repo.replace_all([_item(1, price_buy=None, price_sell=None, emoji=None, updated_at=None)])

    item = (await repo.all())[0]

    assert item.price_buy is None
    assert item.price_sell is None
    assert item.emoji is None
    assert item.updated_at is None
