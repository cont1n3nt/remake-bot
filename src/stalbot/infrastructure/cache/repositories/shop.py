"""SQLite-backed shop: categories, items, purchases and player effects.

заявка 13.09.2026 п.2, migration 0012. One repository for all four tables,
same reasoning as `ShelterRepository`: they exist to serve one flow (buy →
attach → apply → refund) and nothing reads any of them independently.
"""

from collections.abc import Sequence
from datetime import datetime

import aiosqlite

from stalbot.domain.entities.shop import PlayerEffect, ShopCategory, ShopItem, ShopPurchase
from stalbot.infrastructure.cache.db import transaction


class ShopRepository:
    """CRUD over the shop tables."""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        """Wrap an already-open cache connection.

        Args:
            connection: Connection returned by `CacheDb.connect()`.
        """
        self._conn = connection

    # --- categories --------------------------------------------------------

    async def categories(self, *, include_inactive: bool = False) -> Sequence[ShopCategory]:
        """Every category, in display order.

        Args:
            include_inactive: Whether to include hidden categories.
        """
        query = "SELECT * FROM shop_categories"
        if not include_inactive:
            query += " WHERE active = 1"
        query += " ORDER BY sort_order, key"
        cursor = await self._conn.execute(query)
        return [_row_to_category(row) async for row in cursor]

    async def get_category(self, key: str) -> ShopCategory | None:
        """Look up one category.

        Args:
            key: `shop_categories.key`.
        """
        cursor = await self._conn.execute("SELECT * FROM shop_categories WHERE key = ?", (key,))
        row = await cursor.fetchone()
        return _row_to_category(row) if row is not None else None

    async def upsert_category(self, category: ShopCategory, *, now: datetime) -> None:
        """Insert or update one category.

        Args:
            category: The category as it should end up.
            now: Timestamp for `created_at`/`updated_at`.
        """
        async with transaction(self._conn):
            await self._conn.execute(
                """
                INSERT INTO shop_categories
                    (key, name, description, sort_order, active, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (key) DO UPDATE SET
                    name = excluded.name,
                    description = excluded.description,
                    sort_order = excluded.sort_order,
                    active = excluded.active,
                    updated_at = ?
                """,
                (
                    category.key,
                    category.name,
                    category.description,
                    category.sort_order,
                    int(category.active),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )

    # --- items -------------------------------------------------------------

    async def items(
        self, *, category_key: str | None = None, include_hidden: bool = False
    ) -> Sequence[ShopItem]:
        """Items on sale, optionally narrowed to one category.

        Args:
            category_key: Restrict to one category, or `None` for all.
            include_hidden: Include inactive and soft-deleted items — the
                admin editor needs them, the shelf must not show them.
        """
        clauses = [] if include_hidden else ["active = 1", "deleted_at IS NULL"]
        params: list[object] = []
        if category_key is not None:
            clauses.append("category_key = ?")
            params.append(category_key)
        query = "SELECT * FROM shop_items"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY sort_order, id"
        cursor = await self._conn.execute(query, params)
        return [_row_to_item(row) async for row in cursor]

    async def get_item(self, item_id: int) -> ShopItem | None:
        """Look up one item, soft-deleted ones included.

        Args:
            item_id: `shop_items.id`.
        """
        cursor = await self._conn.execute("SELECT * FROM shop_items WHERE id = ?", (item_id,))
        row = await cursor.fetchone()
        return _row_to_item(row) if row is not None else None

    async def find_item_by_name(self, name_norm: str) -> ShopItem | None:
        """Look up a live item by normalized name — the duplicate check.

        Args:
            name_norm: Normalized name.
        """
        cursor = await self._conn.execute(
            "SELECT * FROM shop_items WHERE name_norm = ? AND deleted_at IS NULL", (name_norm,)
        )
        row = await cursor.fetchone()
        return _row_to_item(row) if row is not None else None

    async def insert_item(self, item: ShopItem, *, now: datetime) -> int:
        """Insert one item, returning its assigned id.

        Args:
            item: The item to persist. `item.id` is ignored.
            now: Timestamp for `created_at`.
        """
        async with transaction(self._conn):
            cursor = await self._conn.execute(
                """
                INSERT INTO shop_items
                    (category_key, name, name_norm, description, price_coins, effect_kind,
                     effect_value, duration_days, uses, stock, per_player_limit, emoji,
                     sort_order, active, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.category_key,
                    item.name,
                    item.name_norm,
                    item.description,
                    item.price_coins,
                    item.effect_kind,
                    item.effect_value,
                    item.duration_days,
                    item.uses,
                    item.stock,
                    item.per_player_limit,
                    item.emoji,
                    item.sort_order,
                    int(item.active),
                    now.isoformat(),
                ),
            )
            new_id = cursor.lastrowid
        assert new_id is not None  # noqa: S101 - lastrowid is set right after a successful INSERT
        return new_id

    async def update_item(self, item: ShopItem, *, now: datetime) -> None:
        """Overwrite one item's editable fields.

        Args:
            item: The item as it should end up. `item.id` must be set.
            now: Timestamp for `updated_at`.
        """
        assert item.id is not None  # noqa: S101 - updating requires a persisted item
        async with transaction(self._conn):
            await self._conn.execute(
                """
                UPDATE shop_items
                   SET category_key = ?, name = ?, name_norm = ?, description = ?,
                       price_coins = ?, effect_kind = ?, effect_value = ?, duration_days = ?,
                       uses = ?, stock = ?, per_player_limit = ?, emoji = ?, sort_order = ?,
                       active = ?, updated_at = ?
                 WHERE id = ?
                """,
                (
                    item.category_key,
                    item.name,
                    item.name_norm,
                    item.description,
                    item.price_coins,
                    item.effect_kind,
                    item.effect_value,
                    item.duration_days,
                    item.uses,
                    item.stock,
                    item.per_player_limit,
                    item.emoji,
                    item.sort_order,
                    int(item.active),
                    now.isoformat(),
                    item.id,
                ),
            )

    async def soft_delete_item(self, item_id: int, *, now: datetime) -> None:
        """Hide an item without losing what was already bought through it.

        `shop_purchases.shop_item_id` is `ON DELETE RESTRICT` precisely so
        this cannot become a hard delete by accident: a purchase must keep
        pointing at the thing it bought.

        Args:
            item_id: `shop_items.id`.
            now: Timestamp for `deleted_at`.
        """
        async with transaction(self._conn):
            await self._conn.execute(
                "UPDATE shop_items SET deleted_at = ?, updated_at = ? WHERE id = ?",
                (now.isoformat(), now.isoformat(), item_id),
            )

    async def decrement_stock(self, item_id: int, *, now: datetime) -> None:
        """Take one off the shelf, never below zero.

        Args:
            item_id: `shop_items.id`.
            now: Timestamp for `updated_at`.
        """
        async with transaction(self._conn):
            await self._conn.execute(
                "UPDATE shop_items SET stock = MAX(stock - 1, 0), updated_at = ? "
                "WHERE id = ? AND stock IS NOT NULL",
                (now.isoformat(), item_id),
            )

    async def increment_stock(self, item_id: int, *, now: datetime) -> None:
        """Put one back on the shelf, for a refund.

        Args:
            item_id: `shop_items.id`.
            now: Timestamp for `updated_at`.
        """
        async with transaction(self._conn):
            await self._conn.execute(
                "UPDATE shop_items SET stock = stock + 1, updated_at = ? "
                "WHERE id = ? AND stock IS NOT NULL",
                (now.isoformat(), item_id),
            )

    # --- purchases ---------------------------------------------------------

    async def insert_purchase(self, purchase: ShopPurchase, *, now: datetime) -> int:
        """Record one purchase, returning its assigned id.

        Args:
            purchase: The purchase to persist. `purchase.id` is ignored.
            now: Timestamp for `purchased_at`/`created_at`.
        """
        async with transaction(self._conn):
            cursor = await self._conn.execute(
                """
                INSERT INTO shop_purchases
                    (player_id, shop_item_id, price_coins, status, purchased_at,
                     idempotency_key, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    purchase.player_id,
                    purchase.shop_item_id,
                    purchase.price_coins,
                    purchase.status,
                    now.isoformat(),
                    purchase.idempotency_key,
                    now.isoformat(),
                ),
            )
            new_id = cursor.lastrowid
        assert new_id is not None  # noqa: S101 - lastrowid is set right after a successful INSERT
        return new_id

    async def get_purchase(self, purchase_id: int) -> ShopPurchase | None:
        """Look up one purchase.

        Args:
            purchase_id: `shop_purchases.id`.
        """
        cursor = await self._conn.execute(
            "SELECT * FROM shop_purchases WHERE id = ?", (purchase_id,)
        )
        row = await cursor.fetchone()
        return _row_to_purchase(row) if row is not None else None

    async def find_purchase_by_key(self, idempotency_key: str) -> ShopPurchase | None:
        """Look up a purchase by its idempotency key — the double-click guard.

        Args:
            idempotency_key: The key recorded at purchase time.
        """
        cursor = await self._conn.execute(
            "SELECT * FROM shop_purchases WHERE idempotency_key = ?", (idempotency_key,)
        )
        row = await cursor.fetchone()
        return _row_to_purchase(row) if row is not None else None

    async def purchases_for_player(
        self, player_id: int, *, limit: int | None = None
    ) -> Sequence[ShopPurchase]:
        """One player's purchases, newest first.

        Args:
            player_id: `players.id`.
            limit: Cap the result, or `None` for all of them.
        """
        query = (
            "SELECT * FROM shop_purchases WHERE player_id = ? ORDER BY purchased_at DESC, id DESC"
        )
        params: list[object] = [player_id]
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        cursor = await self._conn.execute(query, params)
        return [_row_to_purchase(row) async for row in cursor]

    async def recent_purchases(self, *, limit: int = 25) -> Sequence[ShopPurchase]:
        """The latest purchases across everyone — the admin's refund picker.

        Args:
            limit: How many to return.
        """
        cursor = await self._conn.execute(
            "SELECT * FROM shop_purchases ORDER BY purchased_at DESC, id DESC LIMIT ?", (limit,)
        )
        return [_row_to_purchase(row) async for row in cursor]

    async def count_purchases(self, player_id: int, shop_item_id: int) -> int:
        """How many times a player has bought one item, refunds excluded.

        A refunded purchase does not count against a per-player limit — it
        is as if it never happened, which is what a refund means.

        Args:
            player_id: `players.id`.
            shop_item_id: `shop_items.id`.
        """
        cursor = await self._conn.execute(
            "SELECT COUNT(*) AS total FROM shop_purchases "
            "WHERE player_id = ? AND shop_item_id = ? AND status <> 'refunded'",
            (player_id, shop_item_id),
        )
        row = await cursor.fetchone()
        return int(row["total"]) if row is not None else 0

    async def set_purchase_status(
        self,
        purchase_id: int,
        status: str,
        *,
        now: datetime,
        refunded_by: int | None = None,
    ) -> None:
        """Move a purchase to another status.

        Args:
            purchase_id: `shop_purchases.id`.
            status: `active` | `used` | `expired` | `refunded`.
            now: Timestamp, recorded as `refunded_at` on a refund.
            refunded_by: Who refunded it, for a refund.
        """
        async with transaction(self._conn):
            if status == "refunded":
                await self._conn.execute(
                    "UPDATE shop_purchases SET status = ?, refunded_at = ?, refunded_by = ? "
                    "WHERE id = ?",
                    (status, now.isoformat(), refunded_by, purchase_id),
                )
            else:
                await self._conn.execute(
                    "UPDATE shop_purchases SET status = ? WHERE id = ?", (status, purchase_id)
                )

    # --- effects -----------------------------------------------------------

    async def insert_effect(self, effect: PlayerEffect, *, now: datetime) -> int:
        """Attach an effect to a player, returning its assigned id.

        Args:
            effect: The effect to persist. `effect.id` is ignored.
            now: Timestamp for `created_at`.
        """
        async with transaction(self._conn):
            cursor = await self._conn.execute(
                """
                INSERT INTO player_effects
                    (player_id, purchase_id, effect_kind, effect_value, uses_left,
                     expires_at, note, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    effect.player_id,
                    effect.purchase_id,
                    effect.effect_kind,
                    effect.effect_value,
                    effect.uses_left,
                    effect.expires_at.isoformat() if effect.expires_at is not None else None,
                    effect.note,
                    now.isoformat(),
                ),
            )
            new_id = cursor.lastrowid
        assert new_id is not None  # noqa: S101 - lastrowid is set right after a successful INSERT
        return new_id

    async def live_effects(self, player_id: int, *, now: datetime) -> Sequence[PlayerEffect]:
        """Every effect still applying to a player at *now*.

        Expiry is filtered in SQL rather than in Python because this is the
        query every ticket confirmation runs, and "not yet expired" is
        exactly what it means to still hold a perk.

        Args:
            player_id: `players.id`.
            now: The moment being asked about, tz-aware.
        """
        cursor = await self._conn.execute(
            "SELECT * FROM player_effects "
            "WHERE player_id = ? AND consumed_at IS NULL "
            "  AND (expires_at IS NULL OR expires_at > ?) "
            "  AND (uses_left IS NULL OR uses_left > 0) "
            "ORDER BY created_at, id",
            (player_id, now.isoformat()),
        )
        return [_row_to_effect(row) async for row in cursor]

    async def effects_for_purchase(self, purchase_id: int) -> Sequence[PlayerEffect]:
        """Every effect one purchase attached — what a refund has to undo.

        Args:
            purchase_id: `shop_purchases.id`.
        """
        cursor = await self._conn.execute(
            "SELECT * FROM player_effects WHERE purchase_id = ? ORDER BY id", (purchase_id,)
        )
        return [_row_to_effect(row) async for row in cursor]

    async def consume_effect(self, effect_id: int, *, now: datetime) -> None:
        """Spend one use, marking the effect consumed when none are left.

        Args:
            effect_id: `player_effects.id`.
            now: Timestamp for `consumed_at`.
        """
        async with transaction(self._conn):
            await self._conn.execute(
                "UPDATE player_effects SET uses_left = MAX(uses_left - 1, 0) "
                "WHERE id = ? AND uses_left IS NOT NULL",
                (effect_id,),
            )
            await self._conn.execute(
                "UPDATE player_effects SET consumed_at = ? "
                "WHERE id = ? AND consumed_at IS NULL AND uses_left = 0",
                (now.isoformat(), effect_id),
            )

    async def expire_effect(self, effect_id: int, *, now: datetime) -> None:
        """Mark an effect spent outright — a refund, or a one-shot perk used.

        Args:
            effect_id: `player_effects.id`.
            now: Timestamp for `consumed_at`.
        """
        async with transaction(self._conn):
            await self._conn.execute(
                "UPDATE player_effects SET consumed_at = ? WHERE id = ? AND consumed_at IS NULL",
                (now.isoformat(), effect_id),
            )


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


def _row_to_category(row: aiosqlite.Row) -> ShopCategory:
    return ShopCategory(
        key=row["key"],
        name=row["name"],
        description=row["description"],
        sort_order=row["sort_order"],
        active=bool(row["active"]),
        created_at=_parse(row["created_at"]),
        updated_at=_parse(row["updated_at"]),
    )


def _row_to_item(row: aiosqlite.Row) -> ShopItem:
    return ShopItem(
        id=row["id"],
        category_key=row["category_key"],
        name=row["name"],
        name_norm=row["name_norm"],
        description=row["description"],
        price_coins=row["price_coins"],
        effect_kind=row["effect_kind"],
        effect_value=row["effect_value"],
        duration_days=row["duration_days"],
        uses=row["uses"],
        stock=row["stock"],
        per_player_limit=row["per_player_limit"],
        emoji=row["emoji"],
        sort_order=row["sort_order"],
        active=bool(row["active"]),
        created_at=_parse(row["created_at"]),
        updated_at=_parse(row["updated_at"]),
        deleted_at=_parse(row["deleted_at"]),
    )


def _row_to_purchase(row: aiosqlite.Row) -> ShopPurchase:
    return ShopPurchase(
        id=row["id"],
        player_id=row["player_id"],
        shop_item_id=row["shop_item_id"],
        price_coins=row["price_coins"],
        status=row["status"],
        purchased_at=_parse(row["purchased_at"]),
        refunded_at=_parse(row["refunded_at"]),
        refunded_by=row["refunded_by"],
        idempotency_key=row["idempotency_key"],
        created_at=_parse(row["created_at"]),
    )


def _row_to_effect(row: aiosqlite.Row) -> PlayerEffect:
    return PlayerEffect(
        id=row["id"],
        player_id=row["player_id"],
        purchase_id=row["purchase_id"],
        effect_kind=row["effect_kind"],
        effect_value=row["effect_value"],
        uses_left=row["uses_left"],
        expires_at=_parse(row["expires_at"]),
        note=row["note"],
        created_at=_parse(row["created_at"]),
        consumed_at=_parse(row["consumed_at"]),
    )
