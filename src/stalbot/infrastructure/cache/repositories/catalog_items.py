"""SQLite-backed `catalog_items` (sqlite_migration.md §IV.2, Э3/Э4).

Named for the table it wraps, not `ItemsRepository` — that name is taken
by the legacy Sheets-cache repository (`items.py`), which this coexists
with until a future migration drops it (§X).
"""

from collections.abc import Iterable, Sequence
from dataclasses import replace
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

    async def insert(self, item: CatalogItem) -> CatalogItem:
        """Insert one item, returning it with its assigned id (`/item_add`, Э7).

        Args:
            item: The item to insert. `item.id` is ignored.
        """
        async with transaction(self._conn):
            cursor = await self._conn.execute(_INSERT_SQL, _item_to_params(item))
            new_id = cursor.lastrowid
        assert new_id is not None  # noqa: S101 - lastrowid is always set right after a successful INSERT
        return replace(item, id=new_id)

    async def by_category(
        self, category: ItemCategory, *, include_deleted: bool = False
    ) -> Sequence[CatalogItem]:
        """Return every item of a given category, ordered by id.

        Args:
            category: Category to filter by.
            include_deleted: Whether to include soft-deleted items.
        """
        query = "SELECT * FROM catalog_items WHERE category = ?"
        if not include_deleted:
            query += " AND deleted_at IS NULL"
        query += " ORDER BY id"
        cursor = await self._conn.execute(query, (category.value,))
        return [_row_to_item(row) async for row in cursor]

    async def get_by_ids(self, item_ids: Iterable[int]) -> dict[int, CatalogItem]:
        """Look up several items by id in one query (avoids an N+1 lookup loop).

        Args:
            item_ids: Ids to look up; duplicates are fine.

        Returns:
            Mapping from id to `CatalogItem`, containing only the ids that
            were actually found (including soft-deleted ones).
        """
        unique_ids = list(dict.fromkeys(item_ids))
        if not unique_ids:
            return {}
        placeholders = ",".join("?" * len(unique_ids))
        cursor = await self._conn.execute(
            f"SELECT * FROM catalog_items WHERE id IN ({placeholders})",  # noqa: S608 - `?` only
            unique_ids,
        )
        return {row["id"]: _row_to_item(row) async for row in cursor}

    async def soft_delete(self, item_id: int, *, now: datetime) -> None:
        """Mark an item deleted without reusing or renumbering its id (`/del_item`, Э7).

        sqlite_migration.md §III.3: unlike the sheet-era block, ids are
        never renumbered — a soft-deleted id simply stops appearing in
        `all()`/`find()`/`by_category()`'s default (non-`include_deleted`) view.

        Args:
            item_id: The item to remove.
            now: Timestamp for both `deleted_at` and `updated_at`.
        """
        async with transaction(self._conn):
            await self._conn.execute(
                "UPDATE catalog_items SET deleted_at = ?, updated_at = ? WHERE id = ?",
                (now.isoformat(), now.isoformat(), item_id),
            )

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

    async def set_section(self, item_id: int, section: str | None, *, now: datetime) -> None:
        """Set (or clear) an item's crafting-profession section (заявка 21.08.2026 п.2, п.6).

        Args:
            item_id: The catalog item to update.
            section: The section name (e.g. "Кулинария"), or `None`.
            now: Timestamp for `updated_at`.
        """
        async with transaction(self._conn):
            await self._conn.execute(
                "UPDATE catalog_items SET section = ?, updated_at = ? WHERE id = ?",
                (section, now.isoformat(), item_id),
            )

    async def set_shelter_item_id(
        self, item_id: int, shelter_item_id: int | None, *, now: datetime
    ) -> None:
        """Set (or clear) an item's bridge to the game reference (Э5 §II.4).

        Args:
            item_id: The catalog item to update.
            shelter_item_id: The matched `shelter_items.id`, or `None` if
                the mapping was never confirmed / needs to be undone.
            now: Timestamp for `updated_at`.
        """
        async with transaction(self._conn):
            await self._conn.execute(
                "UPDATE catalog_items SET shelter_item_id = ?, updated_at = ? WHERE id = ?",
                (shelter_item_id, now.isoformat(), item_id),
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
