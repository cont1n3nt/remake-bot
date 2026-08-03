"""SQLite-backed cache of the `item database` block (PLAN.md §8.1)."""

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

import aiosqlite

from stalbot.domain.entities.item import Item
from stalbot.domain.enums import ItemCategory


def normalize_item_name(name: str) -> str:
    """Collapse whitespace and lower-case an item name for lookup/uniqueness."""
    return " ".join(name.split()).lower()


class ItemsCacheRepository:
    """Read-mostly cache of `Item`s, kept in sync from Sheets by `CacheSync`."""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        """Wrap an already-open cache connection.

        Args:
            connection: Connection returned by `CacheDb.connect()`.
        """
        self._conn = connection

    async def all(self) -> Sequence[Item]:
        """Return every cached item."""
        cursor = await self._conn.execute("SELECT * FROM items ORDER BY id")
        return [_row_to_item(row) async for row in cursor]

    async def by_category(self, category: ItemCategory) -> Sequence[Item]:
        """Return every cached item of a given category, ordered by id.

        Args:
            category: Category to filter by.
        """
        cursor = await self._conn.execute(
            "SELECT * FROM items WHERE category = ? ORDER BY id", (category.value,)
        )
        return [_row_to_item(row) async for row in cursor]

    async def get_by_id(self, item_id: int) -> Item | None:
        """Look up one item by its catalog id.

        Args:
            item_id: The catalog id.
        """
        cursor = await self._conn.execute("SELECT * FROM items WHERE id = ?", (item_id,))
        row = await cursor.fetchone()
        return _row_to_item(row) if row is not None else None

    async def find(self, name: str, category: ItemCategory | None) -> Item | None:
        """Look up one item by name, optionally scoped to a category.

        Args:
            name: Item name (matched case/whitespace-insensitively).
            category: Restrict the lookup, or `None` to match either
                category (raising if the name is ambiguous between them is
                the caller's job — this returns the first match).

        Returns:
            The matching item, or `None` if not found.
        """
        name_norm = normalize_item_name(name)
        if category is not None:
            cursor = await self._conn.execute(
                "SELECT * FROM items WHERE name_norm = ? AND category = ?",
                (name_norm, category.value),
            )
        else:
            cursor = await self._conn.execute(
                "SELECT * FROM items WHERE name_norm = ? ORDER BY id", (name_norm,)
            )
        row = await cursor.fetchone()
        return _row_to_item(row) if row is not None else None

    async def save_delete_backup(self, payload: str) -> None:
        """Persist a pre-mutation snapshot of the catalog block before `/del_item` rewrites it.

        A safety net, not automatic rollback (PLAN.md §7.5): if the
        subsequent `batchUpdate`/read-back fails partway, this snapshot
        lets an operator manually restore the block instead of the bot
        silently having wiped it.

        Args:
            payload: A JSON-serialized snapshot of the block before the write.
        """
        await self._conn.execute(
            """
            INSERT INTO sync_meta (key, value) VALUES ('item_delete_backup', ?)
            ON CONFLICT (key) DO UPDATE SET value = excluded.value
            """,
            (payload,),
        )
        await self._conn.commit()

    async def replace_all(self, items: Sequence[Item]) -> None:
        """Atomically replace the entire cached catalog (full sync).

        Args:
            items: The complete, current catalog read from Sheets.
        """
        await self._conn.execute("DELETE FROM items")
        await self._upsert_many(items)
        await self._conn.commit()

    async def upsert_many(self, items: Sequence[Item]) -> None:
        """Insert or update a subset of items (point refresh).

        Args:
            items: Items to upsert; existing rows with the same id are
                overwritten.
        """
        await self._upsert_many(items)
        await self._conn.commit()

    async def _upsert_many(self, items: Sequence[Item]) -> None:
        await self._conn.executemany(
            """
            INSERT INTO items (id, name, name_norm, category, price_buy, price_sell,
                                emoji, updated_at, sheet_row)
            VALUES (:id, :name, :name_norm, :category, :price_buy, :price_sell,
                    :emoji, :updated_at, :sheet_row)
            ON CONFLICT (id) DO UPDATE SET
                name = excluded.name,
                name_norm = excluded.name_norm,
                category = excluded.category,
                price_buy = excluded.price_buy,
                price_sell = excluded.price_sell,
                emoji = excluded.emoji,
                updated_at = excluded.updated_at,
                sheet_row = excluded.sheet_row
            """,
            [_item_to_params(item) for item in items],
        )


def _item_to_params(item: Item) -> dict[str, object]:
    return {
        "id": item.id,
        "name": item.name,
        "name_norm": normalize_item_name(item.name),
        "category": item.category.value,
        "price_buy": str(item.price_buy) if item.price_buy is not None else None,
        "price_sell": str(item.price_sell) if item.price_sell is not None else None,
        "emoji": item.emoji,
        "updated_at": item.updated_at.isoformat() if item.updated_at is not None else None,
        "sheet_row": item.row,
    }


def _row_to_item(row: aiosqlite.Row) -> Item:
    return Item(
        id=row["id"],
        name=row["name"],
        category=ItemCategory(row["category"]),
        price_buy=Decimal(row["price_buy"]) if row["price_buy"] is not None else None,
        price_sell=Decimal(row["price_sell"]) if row["price_sell"] is not None else None,
        emoji=row["emoji"],
        updated_at=(
            datetime.fromisoformat(row["updated_at"]) if row["updated_at"] is not None else None
        ),
        row=row["sheet_row"],
    )
