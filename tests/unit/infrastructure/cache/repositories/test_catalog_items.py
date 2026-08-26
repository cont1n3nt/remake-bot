"""Tests for `CatalogItemsRepository` against a real (temp-file) SQLite connection."""

from datetime import UTC, datetime

import aiosqlite
import pytest

from stalbot.domain.entities.catalog_item import CatalogItem
from stalbot.domain.enums import ItemCategory
from stalbot.domain.money import Rub
from stalbot.infrastructure.cache.repositories.catalog_items import CatalogItemsRepository

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


def _item(name: str, category: ItemCategory, **overrides: object) -> CatalogItem:
    base: dict[str, object] = dict(
        id=None,
        name=name,
        name_norm=name.lower(),
        category=category,
        section=None,
        price_buy=Rub(1000) if category is ItemCategory.RESOURCE else None,
        price_sell=Rub(2000) if category is ItemCategory.BOOST else None,
        emoji=None,
        sort_order=0,
        shelter_item_id=None,
        created_at=NOW,
        updated_at=None,
        deleted_at=None,
    )
    base.update(overrides)
    return CatalogItem(**base)  # type: ignore[arg-type]


async def test_insert_many_then_all(connection: aiosqlite.Connection) -> None:
    repo = CatalogItemsRepository(connection)
    items = [
        _item("Уха", ItemCategory.RESOURCE),
        _item("Уха", ItemCategory.BOOST),
    ]

    await repo.insert_many(items)

    all_items = await repo.all()
    assert len(all_items) == 2
    assert await repo.count() == 2


async def test_find_by_name_and_category(connection: aiosqlite.Connection) -> None:
    repo = CatalogItemsRepository(connection)
    await repo.insert_many([_item("Уха", ItemCategory.RESOURCE)])

    found = await repo.find("уха", ItemCategory.RESOURCE)

    assert found is not None
    assert found.name == "Уха"
    assert found.price_buy == 1000
    assert await repo.find("уха", ItemCategory.BOOST) is None


async def test_same_name_both_categories_is_allowed(connection: aiosqlite.Connection) -> None:
    """Уха/Морфин/Топот/Гром legitimately exist in both categories (§I.5)."""
    repo = CatalogItemsRepository(connection)
    await repo.insert_many([_item("Уха", ItemCategory.RESOURCE), _item("Уха", ItemCategory.BOOST)])

    resource = await repo.find("уха", ItemCategory.RESOURCE)
    boost = await repo.find("уха", ItemCategory.BOOST)
    assert resource is not None
    assert boost is not None
    assert resource.category is ItemCategory.RESOURCE
    assert boost.category is ItemCategory.BOOST


async def test_duplicate_name_and_category_is_rejected(connection: aiosqlite.Connection) -> None:
    repo = CatalogItemsRepository(connection)
    with pytest.raises(aiosqlite.IntegrityError):
        await repo.insert_many(
            [_item("Уха", ItemCategory.RESOURCE), _item("уха", ItemCategory.RESOURCE)]
        )


async def test_resource_with_a_sell_price_is_rejected(connection: aiosqlite.Connection) -> None:
    repo = CatalogItemsRepository(connection)
    with pytest.raises(aiosqlite.IntegrityError):
        await repo.insert_many([_item("Broken", ItemCategory.RESOURCE, price_sell=Rub(100))])


async def test_soft_deleted_items_excluded_by_default(connection: aiosqlite.Connection) -> None:
    repo = CatalogItemsRepository(connection)
    await repo.insert_many([_item("Аминокислота", ItemCategory.RESOURCE, deleted_at=NOW)])

    assert await repo.all() == []
    assert await repo.count() == 0
    assert await repo.all(include_deleted=True) != []
    assert await repo.count(include_deleted=True) == 1
    assert await repo.find("аминокислота", ItemCategory.RESOURCE) is None


async def test_set_price_updates_and_stamps_updated_at(connection: aiosqlite.Connection) -> None:
    repo = CatalogItemsRepository(connection)
    await repo.insert_many([_item("Уха", ItemCategory.RESOURCE, price_buy=Rub(100))])
    item = await repo.find("уха", ItemCategory.RESOURCE)
    assert item is not None and item.id is not None

    later = datetime(2026, 8, 11, tzinfo=UTC)
    await repo.set_price(item.id, price_buy=Rub(150), price_sell=None, now=later)

    updated = await repo.get_by_id(item.id)
    assert updated is not None
    assert updated.price_buy == 150
    assert updated.updated_at == later


async def test_set_section_updates_and_stamps_updated_at(connection: aiosqlite.Connection) -> None:
    repo = CatalogItemsRepository(connection)
    await repo.insert_many([_item("Топот", ItemCategory.BOOST, price_sell=Rub(300000))])
    item = await repo.find("топот", ItemCategory.BOOST)
    assert item is not None and item.id is not None

    later = datetime(2026, 8, 26, tzinfo=UTC)
    await repo.set_section(item.id, "Кулинария", now=later)

    updated = await repo.get_by_id(item.id)
    assert updated is not None
    assert updated.section == "Кулинария"
    assert updated.updated_at == later
