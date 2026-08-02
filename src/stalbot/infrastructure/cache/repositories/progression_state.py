"""SQLite-backed `progression_state` (PLAN.md §8.1) — cache-only, no Sheets counterpart."""

from datetime import datetime

import aiosqlite

from stalbot.application.dto.progression_state import ProgressionState
from stalbot.domain.nick import NormalizedNick


class ProgressionStateRepository:
    """Tracks the last rank/referral role announced per player (M3)."""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        """Wrap an already-open cache connection.

        Args:
            connection: Connection returned by `CacheDb.connect()`.
        """
        self._conn = connection

    async def get(self, nick: NormalizedNick) -> ProgressionState | None:
        """Return the stored state for a nick, or `None` if never recorded.

        Args:
            nick: Normalized nick.
        """
        cursor = await self._conn.execute(
            "SELECT * FROM progression_state WHERE nick_norm = ?", (nick,)
        )
        row = await cursor.fetchone()
        return _row_to_state(row) if row is not None else None

    async def upsert(self, state: ProgressionState) -> None:
        """Insert or update one player's announced state.

        Args:
            state: The new state to persist.
        """
        await self._conn.execute(
            """
            INSERT INTO progression_state
                (nick_norm, last_rank, last_referral_role, manual_rank_role, announced_at)
            VALUES (:nick_norm, :last_rank, :last_referral_role, :manual_rank_role, :announced_at)
            ON CONFLICT (nick_norm) DO UPDATE SET
                last_rank = excluded.last_rank,
                last_referral_role = excluded.last_referral_role,
                manual_rank_role = excluded.manual_rank_role,
                announced_at = excluded.announced_at
            """,
            {
                "nick_norm": state.nick,
                "last_rank": state.last_rank,
                "last_referral_role": state.last_referral_role,
                "manual_rank_role": int(state.manual_rank_role),
                "announced_at": state.announced_at.isoformat() if state.announced_at else None,
            },
        )
        await self._conn.commit()


def _row_to_state(row: aiosqlite.Row) -> ProgressionState:
    return ProgressionState(
        nick=NormalizedNick(row["nick_norm"]),
        last_rank=row["last_rank"],
        last_referral_role=row["last_referral_role"],
        manual_rank_role=bool(row["manual_rank_role"]),
        announced_at=datetime.fromisoformat(row["announced_at"]) if row["announced_at"] else None,
    )
