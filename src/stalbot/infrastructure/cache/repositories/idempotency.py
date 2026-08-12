"""SQLite-backed `write_idempotency` (sqlite_migration.md §IV.4, Э7) — cache-only.

No Sheets counterpart. Keyed on `deals.id` since migration `0007`
(previously a Sheets row number — see that migration's comment).
"""

import aiosqlite

from stalbot.infrastructure.cache.db import transaction


class IdempotencyRepository:
    """Remembers which `deals.id` a given write key already produced."""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        """Wrap an already-open cache connection.

        Args:
            connection: Connection returned by `CacheDb.connect()`.
        """
        self._conn = connection

    async def get(self, key: str) -> int | None:
        """Return the deal id a key already wrote, or `None` if unseen.

        Args:
            key: The idempotency key (e.g. a Discord interaction id).
        """
        cursor = await self._conn.execute(
            "SELECT deal_id FROM write_idempotency WHERE idempotency_key = ?", (key,)
        )
        row = await cursor.fetchone()
        return int(row["deal_id"]) if row is not None else None

    async def record(self, key: str, deal_id: int, *, created_at: str) -> None:
        """Remember that `key` already wrote `deal_id`.

        Args:
            key: The idempotency key.
            deal_id: The deal it wrote.
            created_at: ISO timestamp.
        """
        async with transaction(self._conn):
            await self._conn.execute(
                "INSERT OR IGNORE INTO write_idempotency (idempotency_key, deal_id, created_at) "
                "VALUES (?, ?, ?)",
                (key, deal_id, created_at),
            )
