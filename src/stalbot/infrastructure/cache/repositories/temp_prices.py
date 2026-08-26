"""SQLite-backed `temp_prices` (заявка 21.08.2026 п.9, migration 0008)."""

from collections.abc import Sequence
from datetime import datetime

import aiosqlite

from stalbot.domain.entities.temp_price import TempPrice
from stalbot.domain.enums import PriceField
from stalbot.domain.money import Rub
from stalbot.infrastructure.cache.db import transaction


class TempPricesRepository:
    """CRUD for active temporary price overrides."""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        """Wrap an already-open cache connection.

        Args:
            connection: Connection returned by `CacheDb.connect()`.
        """
        self._conn = connection

    async def get_active(self, item_id: int, field: PriceField) -> TempPrice | None:
        """Look up the active temp override for one item/field, if any.

        Args:
            item_id: Catalog item id.
            field: Which price field.
        """
        cursor = await self._conn.execute(
            "SELECT * FROM temp_prices WHERE item_id = ? AND field = ?",
            (item_id, field.value),
        )
        row = await cursor.fetchone()
        return _row_to_temp_price(row) if row is not None else None

    async def create(
        self,
        item_id: int,
        field: PriceField,
        original_price: Rub | None,
        expires_at: datetime,
        *,
        created_by: int | None,
        now: datetime,
    ) -> int:
        """Insert a new active temp override, returning its assigned id.

        Args:
            item_id: Catalog item id.
            field: Which price field.
            original_price: The price to revert to once `expires_at` passes.
            expires_at: When the override should revert.
            created_by: Discord id of the admin who set it.
            now: Timestamp for `created_at`.
        """
        async with transaction(self._conn):
            cursor = await self._conn.execute(
                """
                INSERT INTO temp_prices
                    (item_id, field, original_price, expires_at, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    item_id,
                    field.value,
                    int(original_price) if original_price is not None else None,
                    expires_at.isoformat(),
                    created_by,
                    now.isoformat(),
                ),
            )
            row_id = cursor.lastrowid
            assert row_id is not None  # noqa: S101 - lastrowid set right after INSERT
            return row_id

    async def list_due(self, now: datetime) -> Sequence[TempPrice]:
        """Return every temp override whose `expires_at` has passed.

        Args:
            now: The current moment to compare against.
        """
        cursor = await self._conn.execute(
            "SELECT * FROM temp_prices WHERE expires_at <= ?", (now.isoformat(),)
        )
        return [_row_to_temp_price(row) async for row in cursor]

    async def delete(self, temp_price_id: int) -> None:
        """Remove one temp override row (once applied or reverted).

        Args:
            temp_price_id: The row to delete.
        """
        async with transaction(self._conn):
            await self._conn.execute("DELETE FROM temp_prices WHERE id = ?", (temp_price_id,))


def _row_to_temp_price(row: aiosqlite.Row) -> TempPrice:
    return TempPrice(
        id=row["id"],
        item_id=row["item_id"],
        field=PriceField(row["field"]),
        original_price=Rub(row["original_price"]) if row["original_price"] is not None else None,
        expires_at=datetime.fromisoformat(row["expires_at"]),
        created_by=row["created_by"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )
