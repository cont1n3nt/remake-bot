"""SQLite-backed `coin_ledger` (sqlite_migration.md §IV.1, Э3).

Signed Coins adjustments outside the normal deal-reward flow (Э12's shop
spends, manual grants). Empty until Э12 exists; declared and wired now so
`ProgressionRepository`'s aggregation query already reads it.
"""

from collections.abc import Sequence
from datetime import datetime

import aiosqlite

from stalbot.domain.entities.coin_ledger import CoinLedgerEntry
from stalbot.infrastructure.cache.db import transaction


class CoinLedgerRepository:
    """Append-only store of signed Coins adjustments."""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        """Wrap an already-open cache connection.

        Args:
            connection: Connection returned by `CacheDb.connect()`.
        """
        self._conn = connection

    async def add(self, entry: CoinLedgerEntry) -> int:
        """Insert one ledger entry, returning its assigned id.

        Args:
            entry: The entry to persist. `entry.id` is ignored — the
                database assigns it. `entry.delta` must be non-zero
                (`coin_ledger.delta CHECK (delta <> 0)`).
        """
        async with transaction(self._conn):
            cursor = await self._conn.execute(
                """
                INSERT INTO coin_ledger (player_id, delta, reason, created_by, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    entry.player_id,
                    entry.delta,
                    entry.reason,
                    entry.created_by,
                    entry.created_at.isoformat(),
                ),
            )
            new_id = cursor.lastrowid
        assert new_id is not None  # noqa: S101 - lastrowid is always set right after a successful INSERT
        return new_id

    async def for_player(self, player_id: int) -> Sequence[CoinLedgerEntry]:
        """Return every ledger entry for a player, oldest first.

        Args:
            player_id: The player whose entries to list.
        """
        cursor = await self._conn.execute(
            "SELECT * FROM coin_ledger WHERE player_id = ? ORDER BY created_at, id", (player_id,)
        )
        return [_row_to_entry(row) async for row in cursor]

    async def sum_for_player(self, player_id: int) -> int:
        """Return the sum of every ledger entry for a player (0 if none).

        Args:
            player_id: The player to sum.
        """
        cursor = await self._conn.execute(
            "SELECT COALESCE(SUM(delta), 0) AS total FROM coin_ledger WHERE player_id = ?",
            (player_id,),
        )
        row = await cursor.fetchone()
        return int(row["total"]) if row is not None else 0


def _row_to_entry(row: aiosqlite.Row) -> CoinLedgerEntry:
    return CoinLedgerEntry(
        id=row["id"],
        player_id=row["player_id"],
        delta=row["delta"],
        reason=row["reason"],
        created_by=row["created_by"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )
