"""SQLite-backed `players` (sqlite_migration.md §IV.1, Э3).

Replaces `sheet_row`-as-identity: a player is a row with a surrogate `id`,
existing independently of whether they have ever made a deal (§XIV.1). No
Sheets counterpart — cache-only from day one.
"""

from collections.abc import Iterable, Sequence
from datetime import datetime

import aiosqlite

from stalbot.domain.entities.player import Player
from stalbot.domain.nick import NormalizedNick
from stalbot.infrastructure.cache.db import transaction


class PlayersRepository:
    """CRUD for `players`, keyed by surrogate id or normalized nick."""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        """Wrap an already-open cache connection.

        Args:
            connection: Connection returned by `CacheDb.connect()`.
        """
        self._conn = connection

    async def get_by_id(self, player_id: int) -> Player | None:
        """Look up a player by surrogate id.

        Args:
            player_id: The player's `players.id`.
        """
        cursor = await self._conn.execute("SELECT * FROM players WHERE id = ?", (player_id,))
        row = await cursor.fetchone()
        return _row_to_player(row) if row is not None else None

    async def get_by_nick(self, nick: NormalizedNick) -> Player | None:
        """Look up a player by normalized nick.

        Args:
            nick: Normalized nick (see `domain.nick.normalize_nick`).
        """
        cursor = await self._conn.execute("SELECT * FROM players WHERE nick_norm = ?", (nick,))
        row = await cursor.fetchone()
        return _row_to_player(row) if row is not None else None

    async def get_by_discord_id(self, discord_id: int) -> Player | None:
        """Look up a player by bound Discord id.

        Args:
            discord_id: Discord user id.
        """
        cursor = await self._conn.execute(
            "SELECT * FROM players WHERE discord_id = ?", (discord_id,)
        )
        row = await cursor.fetchone()
        return _row_to_player(row) if row is not None else None

    async def all(self) -> Sequence[Player]:
        """Return every player, ordered by id."""
        cursor = await self._conn.execute("SELECT * FROM players ORDER BY id")
        return [_row_to_player(row) async for row in cursor]

    async def get_by_ids(self, ids: Iterable[int]) -> dict[int, Player]:
        """Batch-look-up several players by id, keyed by id (Э6: avoids N+1).

        Args:
            ids: Player ids to look up. Duplicates are harmless.
        """
        id_list = list(dict.fromkeys(ids))
        if not id_list:
            return {}
        placeholders = ",".join("?" * len(id_list))
        cursor = await self._conn.execute(
            f"SELECT * FROM players WHERE id IN ({placeholders})",  # noqa: S608 - `?` placeholders only
            id_list,
        )
        return {row["id"]: _row_to_player(row) async for row in cursor}

    async def list_by_referrer(self, referrer_player_id: int) -> Sequence[Player]:
        """Return every player referred by *referrer_player_id* (Э6, `/referrals`).

        Replaces the sheet-era `TransactionsCacheRepository.list_referral_targets`
        (a per-transaction `referrer_norm` scan) — the new schema tracks the
        referrer on the *referee* directly, so this is a plain lookup.

        Args:
            referrer_player_id: The referring player's id.
        """
        cursor = await self._conn.execute(
            "SELECT * FROM players WHERE referrer_player_id = ? ORDER BY id",
            (referrer_player_id,),
        )
        return [_row_to_player(row) async for row in cursor]

    async def count(self) -> int:
        """Return the total number of players (`/healthcheck`, Э6)."""
        cursor = await self._conn.execute("SELECT COUNT(*) AS n FROM players")
        row = await cursor.fetchone()
        return int(row["n"]) if row is not None else 0

    async def get_or_create(
        self, nick: NormalizedNick, nick_display: str, *, now: datetime
    ) -> Player:
        """Return the player for `nick`, creating one if it doesn't exist yet.

        Idempotent by `nick_norm` (`ux_players_nick`) — the importer (Э4)
        and any live registration path can both call this without racing
        each other into a duplicate.

        Args:
            nick: Normalized nick.
            nick_display: Original-case nick to store if a new row is created.
            now: Timestamp to stamp a newly created row with.
        """
        existing = await self.get_by_nick(nick)
        if existing is not None:
            return existing
        async with transaction(self._conn):
            await self._conn.execute(
                """
                INSERT INTO players (nick_norm, nick_display, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (nick_norm) DO NOTHING
                """,
                (nick, nick_display, now.isoformat(), now.isoformat()),
            )
        # Always re-fetch rather than trust `cursor.lastrowid`: with
        # `ON CONFLICT ... DO NOTHING`, a lost race leaves `lastrowid`
        # pointing at whatever this connection's *previous* successful
        # insert was, not this call's row — silently wrong, not None.
        created = await self.get_by_nick(nick)
        assert created is not None  # noqa: S101 - just inserted (or lost the race to one that did)
        return created

    async def set_discord_id(
        self, player_id: int, discord_id: int | None, *, now: datetime
    ) -> None:
        """Bind (or clear) a player's Discord id.

        Args:
            player_id: The player to update.
            discord_id: Discord user id, or `None` to unbind.
            now: Timestamp for `updated_at`.
        """
        async with transaction(self._conn):
            await self._conn.execute(
                "UPDATE players SET discord_id = ?, updated_at = ? WHERE id = ?",
                (discord_id, now.isoformat(), player_id),
            )

    async def set_referrer(
        self, player_id: int, referrer_player_id: int | None, *, now: datetime
    ) -> None:
        """Set (or clear) a player's resolved referrer.

        Args:
            player_id: The player to update.
            referrer_player_id: The referring player's id, or `None`.
            now: Timestamp for `updated_at`.
        """
        async with transaction(self._conn):
            await self._conn.execute(
                "UPDATE players SET referrer_player_id = ?, updated_at = ? WHERE id = ?",
                (referrer_player_id, now.isoformat(), player_id),
            )

    async def set_booster(self, player_id: int, is_booster: bool, *, now: datetime) -> None:
        """Set a player's server-booster flag.

        Args:
            player_id: The player to update.
            is_booster: The new flag value.
            now: Timestamp for `updated_at`.
        """
        async with transaction(self._conn):
            await self._conn.execute(
                "UPDATE players SET is_booster = ?, updated_at = ? WHERE id = ?",
                (int(is_booster), now.isoformat(), player_id),
            )


def _row_to_player(row: aiosqlite.Row) -> Player:
    return Player(
        id=row["id"],
        nick_norm=NormalizedNick(row["nick_norm"]),
        nick_display=row["nick_display"],
        discord_id=row["discord_id"],
        referrer_player_id=row["referrer_player_id"],
        is_booster=bool(row["is_booster"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )
