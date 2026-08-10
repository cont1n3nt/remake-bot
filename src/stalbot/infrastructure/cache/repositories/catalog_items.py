"""SQLite-backed `catalog_items` (sqlite_migration.md §IV.2, Э3/Э4).

Named for the table it wraps, not `ItemsRepository` — that name is taken
by the legacy Sheets-cache repository (`items.py`), which this coexists
with until a future migration drops it (§X).
"""

from collections.abc import Sequence
from datetime import datetime

import aiosqlite

from stalbot.domain.entities.catalog_item import CatalogItem
from stalbot.domain.enums import ItemCategory
from stalbot.domain.money import Rub
from stalbot.infrastructure.cache.db import transaction


class CatalogItemsRepository:
    """CRUD for `catalog_items`, keyed by surrogate id or `(name_norm, category)`."""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        """Wrap an already-open cache connection.

        Args:
            connection: Connection returned by `CacheDb.connect()`.
        """
        self._conn = connection

    async def get_by_id(self, item_id: int) -> CatalogItem | None:
        """Look up an item by surrogate id, including soft-deleted ones.

        Args:
            item_id: The item's `catalog_items.id`.
        """
        cursor = await self._conn.execute("SELECT * FROM catalog_items WHERE id = ?", (item_id,))
        row = await cursor.fetchone()
        return _row_to_item(row) if row is not None else None

    async def find(self, name_norm: str, category: ItemCategory) -> CatalogItem | None:
        """Look up a non-deleted item by its unique `(name_norm, category)`.

        Args:
            name_norm: Normalized item name (see `repositories.items.normalize_item_name`).
            category: Item category.
        """
        cursor = await self._conn.execute(
            "SELECT * FROM catalog_items WHERE name_norm = ? AND category = ? "
            "AND deleted_at IS NULL",
            (name_norm, category.value),
        )
        row = await cursor.fetchone()
        return _row_to_item(row) if row is not None else None

    async def all(self, *, include_deleted: bool = False) -> Sequence[CatalogItem]:
        """Return every item, ordered by id.

        Args:
            include_deleted: Whether to include soft-deleted items.
        """
        query = "SELECT * FROM catalog_items"
        if not include_deleted:
            query += " WHERE deleted_at IS NULL"
        query += " ORDER BY id"
        cursor = await self._conn.execute(query)
        return [_row_to_item(row) async for row in cursor]

    async def insert_many(self, items: Sequence[CatalogItem]) -> None:
        """Insert several items in one transaction (Э4's importer).

        Args:
            items: Items to insert. `item.id` is ignored — the database
                assigns each a fresh id.
        """
        if not items:
            return
        async with transaction(self._conn):
            await self._conn.executemany(_INSERT_SQL, [_item_to_params(i) for i in items])

    async def count(self, *, include_deleted: bool = False) -> int:
        """Return the number of items — Э4's import-verification count.

        Args:
            include_deleted: Whether to count soft-deleted items too.
        """
        query = "SELECT COUNT(*) AS n FROM catalog_items"
        if not include_deleted:
            query += " WHERE deleted_at IS NULL"
        cursor = await self._conn.execute(query)
        row = await cursor.fetchone()
        return int(row["n"]) if row is not None else 0

    async def set_price(
        self, item_id: int, *, price_buy: Rub | None, price_sell: Rub | None, now: datetime
    ) -> None:
        """Update an item's price(s).

        Args:
            item_id: The item to update.
            price_buy: New buy price (resources only — enforced by the
                table's own `CHECK`).
            price_sell: New sell price (boosts only).
            now: Timestamp for `updated_at`.
        """
        async with transaction(self._conn):
            await self._conn.execute(
                "UPDATE catalog_items SET price_buy = ?, price_sell = ?, updated_at = ? "
                "WHERE id = ?",
                (price_buy, price_sell, now.isoformat(), item_id),
            )


_INSERT_SQL = """
    INSERT INTO catalog_items (
        name, name_norm, category, section, price_buy, price_sell, emoji,
        sort_order, shelter_item_id, created_at, updated_at, deleted_at
    ) VALUES (
        :name, :name_norm, :category, :section, :price_buy, :price_sell, :emoji,
        :sort_order, :shelter_item_id, :created_at, :updated_at, :deleted_at
    )
"""


def _item_to_params(item: CatalogItem) -> dict[str, object]:
    return {
        "name": item.name,
        "name_norm": item.name_norm,
        "category": item.category.value,
        "section": item.section,
        "price_buy": None if item.price_buy is None else int(item.price_buy),
        "price_sell": None if item.price_sell is None else int(item.price_sell),
        "emoji": item.emoji,
        "sort_order": item.sort_order,
        "shelter_item_id": item.shelter_item_id,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat() if item.updated_at is not None else None,
        "deleted_at": item.deleted_at.isoformat() if item.deleted_at is not None else None,
    }


def _row_to_item(row: aiosqlite.Row) -> CatalogItem:
    return CatalogItem(
        id=row["id"],
        name=row["name"],
        name_norm=row["name_norm"],
        category=ItemCategory(row["category"]),
        section=row["section"],
        price_buy=None if row["price_buy"] is None else Rub(row["price_buy"]),
        price_sell=None if row["price_sell"] is None else Rub(row["price_sell"]),
        emoji=row["emoji"],
        sort_order=row["sort_order"],
        shelter_item_id=row["shelter_item_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=(
            datetime.fromisoformat(row["updated_at"]) if row["updated_at"] is not None else None
        ),
        deleted_at=(
            datetime.fromisoformat(row["deleted_at"]) if row["deleted_at"] is not None else None
        ),
    )
