"""SQLite-backed `boost_order_lines` (PLAN.md §8.1, §11.6) — cache-only, no Sheets counterpart."""

from collections.abc import Sequence

import aiosqlite

from stalbot.application.dto.boost_order_line import BoostOrderLine
from stalbot.domain.enums import ItemCategory
from stalbot.infrastructure.cache.db import transaction


class BoostOrderLinesRepository:
    """Persistent draft lines for the in-place boost order editor (M10)."""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        """Wrap an already-open cache connection.

        Args:
            connection: Connection returned by `CacheDb.connect()`.
        """
        self._conn = connection

    async def list_for_channel(self, channel_id: int) -> Sequence[BoostOrderLine]:
        """Return every draft line for a ticket channel.

        Args:
            channel_id: Discord channel id of the order-boosts ticket.
        """
        cursor = await self._conn.execute(
            "SELECT * FROM boost_order_lines WHERE channel_id = ? ORDER BY item_id", (channel_id,)
        )
        return [_row_to_line(row) async for row in cursor]

    async def upsert(self, line: BoostOrderLine) -> None:
        """Insert or update one line's quantity.

        Args:
            line: The line to persist.
        """
        async with transaction(self._conn):
            await self._conn.execute(
                """
                INSERT INTO boost_order_lines
                    (channel_id, item_id, item_name_norm, category, quantity)
                VALUES (:channel_id, :item_id, :item_name_norm, :category, :quantity)
                ON CONFLICT (channel_id, item_id) DO UPDATE SET
                    item_name_norm = excluded.item_name_norm,
                    category = excluded.category,
                    quantity = excluded.quantity
                """,
                {
                    "channel_id": line.channel_id,
                    "item_id": line.item_id,
                    "item_name_norm": line.item_name_norm,
                    "category": line.category.value,
                    "quantity": line.quantity,
                },
            )

    async def delete_line(self, channel_id: int, item_id: int) -> None:
        """Remove a single line from a draft order.

        Args:
            channel_id: Discord channel id.
            item_id: Catalog item id of the line to remove.
        """
        async with transaction(self._conn):
            await self._conn.execute(
                "DELETE FROM boost_order_lines WHERE channel_id = ? AND item_id = ?",
                (channel_id, item_id),
            )

    async def delete_by_name(self, *, name_norm: str, category: ItemCategory) -> Sequence[int]:
        """Remove every draft line referencing a permanently deleted item.

        Args:
            name_norm: Normalized name of the deleted item.
            category: The deleted item's category.

        Returns:
            The distinct channel ids that had a line removed, so the caller
            can consider notifying those drafts' authors.
        """
        cursor = await self._conn.execute(
            "SELECT DISTINCT channel_id FROM boost_order_lines"
            " WHERE item_name_norm = ? AND category = ?",
            (name_norm, category.value),
        )
        channel_ids = [row["channel_id"] async for row in cursor]
        async with transaction(self._conn):
            await self._conn.execute(
                "DELETE FROM boost_order_lines WHERE item_name_norm = ? AND category = ?",
                (name_norm, category.value),
            )
        return channel_ids

    async def clear_channel(self, channel_id: int) -> None:
        """Remove every line for a channel (order confirmed or cancelled).

        Args:
            channel_id: Discord channel id.
        """
        async with transaction(self._conn):
            await self._conn.execute(
                "DELETE FROM boost_order_lines WHERE channel_id = ?", (channel_id,)
            )


def _row_to_line(row: aiosqlite.Row) -> BoostOrderLine:
    return BoostOrderLine(
        channel_id=row["channel_id"],
        item_id=row["item_id"],
        item_name_norm=row["item_name_norm"],
        category=ItemCategory(row["category"]),
        quantity=row["quantity"],
    )
