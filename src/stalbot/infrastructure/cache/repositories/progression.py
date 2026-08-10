"""SQLite-backed `player_progression` (sqlite_migration.md §IV.1, §V.1, Э3).

`aggregates_for_all()` is the one-SQL-query aggregation from §V.1: a
single pass over `deals`/`coin_ledger`/`players` builds every player's
`PlayerAggregates` at once (no N+1 per-player query). `recompute()` feeds
those into `domain.progression.calculator.compute_progression()` and
persists whatever changed — the only place `catalog_items`/`deals`
numbers turn into a stored Coins/XP/rank snapshot.
"""

import json
from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime

import aiosqlite

from stalbot.domain.entities.player_progression import PlayerProgressionRecord
from stalbot.domain.progression.calculator import (
    CALCULATOR_VERSION,
    PlayerAggregates,
    PlayerProgression,
    compute_progression,
)
from stalbot.domain.progression.ranks import RankLadder
from stalbot.domain.progression.referrals import ReferralLadder
from stalbot.infrastructure.cache.db import transaction

#: Booster/big-deal turnover thresholds — mirrors
#: `perks.BOOSTER_BIG_DEAL_THRESHOLDS`/the K3 formula's own literals
#: (§V.1). Kept local rather than imported so this SQL has no import-time
#: dependency on `perks.py`'s constant *shape*, only on the numbers, which
#: the frozen-formula test (`test_calculator_matches_frozen_sheet_formulas.py`)
#: already pins down independently.
_BOOSTER_PURCHASE_THRESHOLD = 10_000_000
_BOOSTER_SALE_THRESHOLD = 25_000_000
_BIG_DEAL_50M = 50_000_000
_BIG_DEAL_100M = 100_000_000

_BOOSTER_FILTER = (
    f"(deal_type='purchase' AND amount >= {_BOOSTER_PURCHASE_THRESHOLD})"
    f" OR (deal_type='sale' AND amount >= {_BOOSTER_SALE_THRESHOLD})"
)

# Every interpolated value above is a module-level int constant, never
# user input — not the string-built-query injection risk S608 guards
# against.
_AGGREGATES_SQL = f"""
    WITH deal_totals AS (
        SELECT player_id,
               COALESCE(SUM(amount) FILTER (WHERE deal_type='purchase'),0) AS m,
               COALESCE(SUM(amount) FILTER (WHERE deal_type='sale'),0)     AS n,
               COUNT(*) FILTER (WHERE {_BOOSTER_FILTER}) AS booster_deals,
               COUNT(*) FILTER (WHERE amount >= {_BIG_DEAL_50M})  AS deals_50m,
               COUNT(*) FILTER (WHERE amount >= {_BIG_DEAL_100M}) AS deals_100m
        FROM deals GROUP BY player_id
    ),
    ledger AS (
        SELECT player_id, COALESCE(SUM(delta),0) AS delta FROM coin_ledger GROUP BY player_id
    ),
    referees AS (
        SELECT p.referrer_player_id AS referrer_id, COUNT(*) AS referral_count,
               COALESCE(SUM(COALESCE(dt.m,0)+COALESCE(dt.n,0)),0) AS referee_turnover
        FROM players p LEFT JOIN deal_totals dt ON dt.player_id = p.id
        WHERE p.referrer_player_id IS NOT NULL GROUP BY p.referrer_player_id
    )
    SELECT p.id AS player_id, p.referrer_player_id, p.is_booster,
           COALESCE(dt.m,0) AS m, COALESCE(dt.n,0) AS n,
           COALESCE(l.delta,0) AS ledger_delta,
           COALESCE(dt.booster_deals,0) AS booster_deals,
           COALESCE(dt.deals_50m,0) AS deals_50m,
           COALESCE(dt.deals_100m,0) AS deals_100m,
           COALESCE(r.referral_count,0) AS referral_count,
           COALESCE(r.referee_turnover,0) AS referee_turnover
    FROM players p
    LEFT JOIN deal_totals dt ON dt.player_id = p.id
    LEFT JOIN ledger      l  ON l.player_id  = p.id
    LEFT JOIN referees    r  ON r.referrer_id = p.id
"""  # noqa: S608
# Aggregates deliberately do NOT filter by date (§V.1): the sheet's own
# SUMIFS never did either, and 534 historical deals only have an
# interpolated date at all (§I.3) — a date filter here would silently
# drop them from turnover the way the old `_parse_ticket_row` bug did.


class ProgressionRepository:
    """Materialized-progression store, plus the aggregation this schema needs it for."""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        """Wrap an already-open cache connection.

        Args:
            connection: Connection returned by `CacheDb.connect()`.
        """
        self._conn = connection

    async def aggregates_for_all(self) -> dict[int, PlayerAggregates]:
        """Compute every player's `PlayerAggregates` in one query (§V.1)."""
        cursor = await self._conn.execute(_AGGREGATES_SQL)
        result: dict[int, PlayerAggregates] = {}
        async for row in cursor:
            result[row["player_id"]] = PlayerAggregates(
                purchase_turnover=row["m"],
                sale_turnover=row["n"],
                coin_ledger_delta=row["ledger_delta"],
                referral_count=row["referral_count"],
                referee_total_turnover=row["referee_turnover"],
                has_referrer=row["referrer_player_id"] is not None,
                is_booster=bool(row["is_booster"]),
                booster_big_deal_count=row["booster_deals"],
                deal_count_over_50m=row["deals_50m"],
                deal_count_over_100m=row["deals_100m"],
            )
        return result

    async def get(self, player_id: int) -> PlayerProgressionRecord | None:
        """Look up the stored progression snapshot for one player.

        Args:
            player_id: The player to look up.
        """
        cursor = await self._conn.execute(
            "SELECT * FROM player_progression WHERE player_id = ?", (player_id,)
        )
        row = await cursor.fetchone()
        return _row_to_record(row) if row is not None else None

    async def all(self) -> Sequence[PlayerProgressionRecord]:
        """Return every stored progression snapshot."""
        cursor = await self._conn.execute("SELECT * FROM player_progression")
        return [_row_to_record(row) async for row in cursor]

    async def upsert(self, record: PlayerProgressionRecord) -> None:
        """Insert or replace one player's progression snapshot.

        Args:
            record: The snapshot to persist.
        """
        async with transaction(self._conn):
            await self._upsert(record)

    async def _upsert(self, record: PlayerProgressionRecord) -> None:
        await self._conn.execute(
            """
            INSERT INTO player_progression (
                player_id, purchase_turnover, sale_turnover, total_turnover,
                referral_count, coins, xp, rank_key, referral_role_key,
                breakdown_json, calculator_version, computed_at
            ) VALUES (
                :player_id, :purchase_turnover, :sale_turnover, :total_turnover,
                :referral_count, :coins, :xp, :rank_key, :referral_role_key,
                :breakdown_json, :calculator_version, :computed_at
            )
            ON CONFLICT (player_id) DO UPDATE SET
                purchase_turnover  = excluded.purchase_turnover,
                sale_turnover      = excluded.sale_turnover,
                total_turnover     = excluded.total_turnover,
                referral_count     = excluded.referral_count,
                coins              = excluded.coins,
                xp                 = excluded.xp,
                rank_key           = excluded.rank_key,
                referral_role_key  = excluded.referral_role_key,
                breakdown_json     = excluded.breakdown_json,
                calculator_version = excluded.calculator_version,
                computed_at        = excluded.computed_at
            """,
            _record_to_params(record),
        )

    async def recompute(
        self, player_ids: Sequence[int] | None = None, *, now: datetime
    ) -> set[int]:
        """Recompute and persist progression for some or all players.

        Args:
            player_ids: Which players to recompute; `None` recomputes
                everyone `aggregates_for_all()` has an aggregate for
                (i.e. every player, since the query left-joins from
                `players`).
            now: Timestamp to stamp changed rows' `computed_at` with.

        Returns:
            The ids of players whose stored snapshot actually changed
            (new or different from what was already there) — the caller
            uses this to decide who to send a promotion announcement to.
        """
        aggregates = await self.aggregates_for_all()
        target_ids = set(player_ids) if player_ids is not None else set(aggregates)
        ranks = RankLadder()
        referrals = ReferralLadder()

        changed: set[int] = set()
        async with transaction(self._conn):
            for player_id in target_ids:
                aggregate = aggregates.get(player_id)
                if aggregate is None:
                    continue
                progression = compute_progression(aggregate, ranks=ranks, referrals=referrals)
                record = _to_record(player_id, progression, now=now)
                existing = await self.get(player_id)
                if existing is None or _differs(existing, record):
                    await self._upsert(record)
                    changed.add(player_id)
        return changed


def _to_record(
    player_id: int, progression: PlayerProgression, *, now: datetime
) -> PlayerProgressionRecord:
    return PlayerProgressionRecord(
        player_id=player_id,
        purchase_turnover=progression.purchase_turnover,
        sale_turnover=progression.sale_turnover,
        total_turnover=progression.total_turnover,
        referral_count=progression.referral_count,
        coins=progression.coins,
        xp=progression.xp,
        rank_key=progression.rank.key if progression.rank is not None else None,
        referral_role_key=(
            progression.referral_role.key if progression.referral_role is not None else None
        ),
        breakdown_json=json.dumps(asdict(progression.breakdown), sort_keys=True),
        calculator_version=CALCULATOR_VERSION,
        computed_at=now,
    )


def _differs(existing: PlayerProgressionRecord, new: PlayerProgressionRecord) -> bool:
    """Compare every field except `computed_at`.

    A no-op recompute must not touch the row (and so must not appear in
    `recompute()`'s changed set).
    """
    return (
        existing.purchase_turnover,
        existing.sale_turnover,
        existing.total_turnover,
        existing.referral_count,
        existing.coins,
        existing.xp,
        existing.rank_key,
        existing.referral_role_key,
        existing.breakdown_json,
        existing.calculator_version,
    ) != (
        new.purchase_turnover,
        new.sale_turnover,
        new.total_turnover,
        new.referral_count,
        new.coins,
        new.xp,
        new.rank_key,
        new.referral_role_key,
        new.breakdown_json,
        new.calculator_version,
    )


def _record_to_params(record: PlayerProgressionRecord) -> dict[str, object]:
    return {
        "player_id": record.player_id,
        "purchase_turnover": record.purchase_turnover,
        "sale_turnover": record.sale_turnover,
        "total_turnover": record.total_turnover,
        "referral_count": record.referral_count,
        "coins": record.coins,
        "xp": record.xp,
        "rank_key": record.rank_key,
        "referral_role_key": record.referral_role_key,
        "breakdown_json": record.breakdown_json,
        "calculator_version": record.calculator_version,
        "computed_at": record.computed_at.isoformat(),
    }


def _row_to_record(row: aiosqlite.Row) -> PlayerProgressionRecord:
    return PlayerProgressionRecord(
        player_id=row["player_id"],
        purchase_turnover=row["purchase_turnover"],
        sale_turnover=row["sale_turnover"],
        total_turnover=row["total_turnover"],
        referral_count=row["referral_count"],
        coins=row["coins"],
        xp=row["xp"],
        rank_key=row["rank_key"],
        referral_role_key=row["referral_role_key"],
        breakdown_json=row["breakdown_json"],
        calculator_version=row["calculator_version"],
        computed_at=datetime.fromisoformat(row["computed_at"]),
    )
