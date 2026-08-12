"""SQLite-backed `item_price_history` (sqlite_migration.md §IV.2, Э7) — cache-only.

No Sheets counterpart. Append-only audit trail of every
`catalog_items.price_buy`/`price_sell`
change — written alongside the price itself by `PricingService`, never read
back by it (a future `/price_history <предмет>` command, if built, would be
the first reader).
"""

from collections.abc import Sequence
from datetime import datetime

import aiosqlite

from stalbot.domain.entities.item_price_history import ItemPriceHistoryEntry
from stalbot.domain.enums import PriceChangeSource, PriceField
from stalbot.domain.money import Rub
from stalbot.infrastructure.cache.db import transaction


class ItemPriceHistoryRepository:
    """Insert-mostly log of price changes."""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        """Wrap an already-open cache connection.

        Args:
            connection: Connection returned by `CacheDb.connect()`.
        """
        self._conn = connection

    async def add(self, entry: ItemPriceHistoryEntry) -> int:
        """Insert one price-change entry, returning its assigned id.

        Args:
            entry: The entry to persist. `entry.id` is ignored.
        """
        async with transaction(self._conn):
            cursor = await self._conn.execute(
                """
                INSERT INTO item_price_history
                    (item_id, field, old_price, new_price, changed_by, source, changed_at)
                VALUES (:item_id, :field, :old_price, :new_price, :changed_by, :source, :changed_at)
                """,
                {
                    "item_id": entry.item_id,
                    "field": entry.field.value,
                    "old_price": None if entry.old_price is None else int(entry.old_price),
                    "new_price": None if entry.new_price is None else int(entry.new_price),
                    "changed_by": entry.changed_by,
                    "source": entry.source.value,
                    "changed_at": entry.changed_at.isoformat(),
                },
            )
            new_id = cursor.lastrowid
        assert new_id is not None  # noqa: S101 - lastrowid is always set right after a successful INSERT
        return new_id

    async def for_item(self, item_id: int) -> Sequence[ItemPriceHistoryEntry]:
        """Return every price change recorded for an item, oldest first.

        Args:
            item_id: The item to look up.
        """
        cursor = await self._conn.execute(
            "SELECT * FROM item_price_history WHERE item_id = ? ORDER BY changed_at, id",
            (item_id,),
        )
        return [_row_to_entry(row) async for row in cursor]


def _row_to_entry(row: aiosqlite.Row) -> ItemPriceHistoryEntry:
    return ItemPriceHistoryEntry(
        id=row["id"],
        item_id=row["item_id"],
        field=PriceField(row["field"]),
        old_price=None if row["old_price"] is None else Rub(row["old_price"]),
        new_price=None if row["new_price"] is None else Rub(row["new_price"]),
        changed_by=row["changed_by"],
        source=PriceChangeSource(row["source"]),
        changed_at=datetime.fromisoformat(row["changed_at"]),
    )
