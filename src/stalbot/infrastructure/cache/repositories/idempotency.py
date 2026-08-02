"""SQLite-backed `write_idempotency` (PLAN.md §7.4 step 5) — cache-only, no Sheets counterpart."""

import aiosqlite


class IdempotencyRepository:
    """Remembers which Sheets row a given write key already produced."""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        """Wrap an already-open cache connection.

        Args:
            connection: Connection returned by `CacheDb.connect()`.
        """
        self._conn = connection

    async def get(self, key: str) -> int | None:
        """Return the sheet row a key already wrote, or `None` if unseen.

        Args:
            key: The idempotency key (e.g. a Discord interaction id).
        """
        cursor = await self._conn.execute(
            "SELECT sheet_row FROM write_idempotency WHERE idempotency_key = ?", (key,)
        )
        row = await cursor.fetchone()
        return int(row["sheet_row"]) if row is not None else None

    async def record(self, key: str, sheet_row: int, *, created_at: str) -> None:
        """Remember that `key` already wrote `sheet_row`.

        Args:
            key: The idempotency key.
            sheet_row: The row it wrote.
            created_at: ISO timestamp.
        """
        await self._conn.execute(
            "INSERT OR IGNORE INTO write_idempotency (idempotency_key, sheet_row, created_at) "
            "VALUES (?, ?, ?)",
            (key, sheet_row, created_at),
        )
        await self._conn.commit()
