"""SQLite-backed cache of the `Общая база пользователей` block (PLAN.md §8.1).

`UserProfile` itself carries no display-cased nick — column `J` on the
sheet is already `LOWER()`-normalized, so there is nothing to read one
from there. The cache's `nick_display` column exists anyway (it is what
`/profile` etc. show), so callers pass a `nick_norm -> display` map
resolved from the `Тикеты` block's original-case `B` column (`CacheSync`
builds this once per sync, since it reads that block too).
"""

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal

import aiosqlite

from stalbot.domain.entities.user_profile import UserProfile
from stalbot.domain.nick import NormalizedNick
from stalbot.infrastructure.cache.db import transaction


class UsersCacheRepository:
    """Read-mostly cache of `UserProfile`s, kept in sync from Sheets by `CacheSync`."""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        """Wrap an already-open cache connection.

        Args:
            connection: Connection returned by `CacheDb.connect()`.
        """
        self._conn = connection

    async def get_by_nick(self, nick: NormalizedNick) -> UserProfile | None:
        """Look up a profile by its normalized nick.

        Args:
            nick: Normalized nick (see `domain.nick.normalize_nick`).
        """
        cursor = await self._conn.execute("SELECT * FROM users WHERE nick_norm = ?", (nick,))
        row = await cursor.fetchone()
        return _row_to_profile(row) if row is not None else None

    async def get_by_nicks(
        self, nicks: Sequence[NormalizedNick]
    ) -> dict[NormalizedNick, UserProfile]:
        """Look up several profiles by nick in one query (APP-6: avoids an N+1 `get_by_nick` loop).

        INFRA1-12: builds one `?` placeholder per nick, and SQLite caps the
        number of parameters in a single statement
        (`SQLITE_MAX_VARIABLE_NUMBER`). No caller today passes anywhere near
        that many nicks (one referral list, one period's traders) — this is
        not chunked, so a future caller must keep its input below that limit
        itself.

        Args:
            nicks: Normalized nicks to look up; duplicates are fine.

        Returns:
            Mapping from nick to `UserProfile`, containing only the nicks
            that were actually found.
        """
        unique_nicks = list(dict.fromkeys(nicks))
        if not unique_nicks:
            return {}
        # The f-string only interpolates a run of "?" placeholders (one per
        # nick) — every actual value still goes through parameter binding,
        # so this isn't the string-built-query injection risk the rule
        # guards against.
        placeholders = ",".join("?" * len(unique_nicks))
        cursor = await self._conn.execute(
            f"SELECT * FROM users WHERE nick_norm IN ({placeholders})",  # noqa: S608
            unique_nicks,
        )
        return {NormalizedNick(row["nick_norm"]): _row_to_profile(row) async for row in cursor}

    async def get_nick_displays(self, nicks: Sequence[NormalizedNick]) -> dict[NormalizedNick, str]:
        """Look up several original-case nicks in one query (APP-6: avoids an N+1 loop).

        INFRA1-12: same unbounded `IN (...)` caveat as `get_by_nicks` — see
        its docstring.

        Args:
            nicks: Normalized nicks to look up; duplicates are fine.

        Returns:
            Mapping from nick to its original-case display text, containing
            only the nicks that were actually found.
        """
        unique_nicks = list(dict.fromkeys(nicks))
        if not unique_nicks:
            return {}
        placeholders = ",".join("?" * len(unique_nicks))  # see get_by_nicks re: S608
        cursor = await self._conn.execute(
            f"SELECT nick_norm, nick_display FROM users WHERE nick_norm IN ({placeholders})",  # noqa: S608
            unique_nicks,
        )
        return {NormalizedNick(row["nick_norm"]): row["nick_display"] async for row in cursor}

    async def get_by_discord_id(self, discord_id: int) -> UserProfile | None:
        """Look up a profile by bound Discord id.

        Args:
            discord_id: Discord user id.
        """
        cursor = await self._conn.execute("SELECT * FROM users WHERE discord_id = ?", (discord_id,))
        row = await cursor.fetchone()
        return _row_to_profile(row) if row is not None else None

    async def all(self) -> Sequence[UserProfile]:
        """Return every cached profile."""
        cursor = await self._conn.execute("SELECT * FROM users ORDER BY sheet_row")
        return [_row_to_profile(row) async for row in cursor]

    async def get_nick_display(self, nick: NormalizedNick) -> str | None:
        """Return the original-case nick stored for a normalized nick.

        Args:
            nick: Normalized nick.
        """
        cursor = await self._conn.execute(
            "SELECT nick_display FROM users WHERE nick_norm = ?", (nick,)
        )
        row = await cursor.fetchone()
        return row["nick_display"] if row is not None else None

    async def last_synced_at(self) -> datetime | None:
        """Return the most recent `synced_at` across all cached users.

        Used to decide whether the cache is stale enough to warrant an
        inline refresh before answering a read (PLAN.md §8.2).
        """
        cursor = await self._conn.execute("SELECT MAX(synced_at) AS ts FROM users")
        row = await cursor.fetchone()
        value = row["ts"] if row is not None else None
        return datetime.fromisoformat(value) if value is not None else None

    async def replace_all(
        self,
        profiles: Sequence[UserProfile],
        *,
        nick_displays: Mapping[NormalizedNick, str],
        synced_at: str,
    ) -> None:
        """Atomically replace the entire cached user base (full sync).

        Args:
            profiles: The complete, current user base read from Sheets.
            nick_displays: `nick_norm -> original-case nick`, resolved from
                the Тикеты block. A profile missing from this map falls
                back to its own (lower-case) nick.
            synced_at: ISO timestamp to stamp every row with.
        """
        async with transaction(self._conn):
            await self._conn.execute("DELETE FROM users")
            await self._upsert_many(profiles, nick_displays=nick_displays, synced_at=synced_at)

    async def upsert_many(
        self,
        profiles: Sequence[UserProfile],
        *,
        nick_displays: Mapping[NormalizedNick, str],
        synced_at: str,
    ) -> None:
        """Insert or update a subset of profiles (point refresh).

        Args:
            profiles: Profiles to upsert.
            nick_displays: See `replace_all`.
            synced_at: ISO timestamp to stamp every row with.
        """
        async with transaction(self._conn):
            await self._upsert_many(profiles, nick_displays=nick_displays, synced_at=synced_at)

    async def _upsert_many(
        self,
        profiles: Sequence[UserProfile],
        *,
        nick_displays: Mapping[NormalizedNick, str],
        synced_at: str,
    ) -> None:
        await self._conn.executemany(
            """
            INSERT INTO users (nick_norm, nick_display, discord_id, coins, xp,
                                buy_turnover, sell_turnover, total_turnover,
                                referrals_count, is_booster, rank, referral_role,
                                sheet_row, synced_at)
            VALUES (:nick_norm, :nick_display, :discord_id, :coins, :xp,
                    :buy_turnover, :sell_turnover, :total_turnover,
                    :referrals_count, :is_booster, :rank, :referral_role,
                    :sheet_row, :synced_at)
            ON CONFLICT (nick_norm) DO UPDATE SET
                nick_display = excluded.nick_display,
                discord_id = excluded.discord_id,
                coins = excluded.coins,
                xp = excluded.xp,
                buy_turnover = excluded.buy_turnover,
                sell_turnover = excluded.sell_turnover,
                total_turnover = excluded.total_turnover,
                referrals_count = excluded.referrals_count,
                is_booster = excluded.is_booster,
                rank = excluded.rank,
                referral_role = excluded.referral_role,
                sheet_row = excluded.sheet_row,
                synced_at = excluded.synced_at
            """,
            [
                _profile_to_params(profile, nick_displays, synced_at=synced_at)
                for profile in profiles
            ],
        )


def _profile_to_params(
    profile: UserProfile, nick_displays: Mapping[NormalizedNick, str], *, synced_at: str
) -> dict[str, object]:
    return {
        "nick_norm": profile.nick,
        "nick_display": nick_displays.get(profile.nick, profile.nick),
        "discord_id": profile.discord_id,
        "coins": profile.coins,
        "xp": profile.xp,
        "buy_turnover": str(profile.buy_turnover),
        "sell_turnover": str(profile.sell_turnover),
        "total_turnover": str(profile.total_turnover),
        "referrals_count": profile.referrals_count,
        "is_booster": int(profile.is_booster),
        "rank": profile.rank,
        "referral_role": profile.referral_role,
        "sheet_row": profile.row,
        "synced_at": synced_at,
    }


def _row_to_profile(row: aiosqlite.Row) -> UserProfile:
    return UserProfile(
        row=row["sheet_row"],
        nick=NormalizedNick(row["nick_norm"]),
        discord_id=row["discord_id"],
        coins=row["coins"],
        xp=row["xp"],
        buy_turnover=(
            Decimal(row["buy_turnover"]) if row["buy_turnover"] is not None else Decimal(0)
        ),
        sell_turnover=(
            Decimal(row["sell_turnover"]) if row["sell_turnover"] is not None else Decimal(0)
        ),
        total_turnover=(
            Decimal(row["total_turnover"]) if row["total_turnover"] is not None else Decimal(0)
        ),
        referrals_count=row["referrals_count"],
        is_booster=bool(row["is_booster"]),
        rank=row["rank"],
        referral_role=row["referral_role"],
    )
