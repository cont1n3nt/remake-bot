"""SQLite-backed `deals` (sqlite_migration.md §IV.1, Э3).

Replaces the sheet-era `transactions` cache: keyed by `player_id`, not a
nick, and every write carries the `rank_at_deal`/`booster_at_deal`
snapshot a bonus's forward-only semantics needs (§XIV.1). No Sheets
counterpart — cache-only from day one.
"""

from collections.abc import Sequence
from datetime import datetime

import aiosqlite

from stalbot.domain.entities.deal import Deal
from stalbot.domain.enums import DealSource, DealType, OccurredAtKind
from stalbot.domain.money import Rub
from stalbot.infrastructure.cache.db import transaction


class DealsRepository:
    """Insert-mostly store of `Deal`s — the trading schema's transaction log."""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        """Wrap an already-open cache connection.

        Args:
            connection: Connection returned by `CacheDb.connect()`.
        """
        self._conn = connection

    async def insert(self, deal: Deal) -> int:
        """Insert one deal, returning its assigned id.

        Args:
            deal: The deal to persist. `deal.id` is ignored — the database
                assigns it.
        """
        async with transaction(self._conn):
            cursor = await self._conn.execute(_INSERT_SQL, _deal_to_params(deal))
            new_id = cursor.lastrowid
        assert new_id is not None  # noqa: S101 - lastrowid is always set right after a successful INSERT
        return new_id

    async def insert_many(self, deals: Sequence[Deal]) -> None:
        """Insert several deals in one transaction (Э4's importer).

        Args:
            deals: Deals to persist, in any order — `legacy_sheet_row`
                preserves the sheet's positional order for later trace/audit.
        """
        if not deals:
            return
        async with transaction(self._conn):
            await self._conn.executemany(_INSERT_SQL, [_deal_to_params(d) for d in deals])

    async def get_by_id(self, deal_id: int) -> Deal | None:
        """Look up one deal by id.

        Args:
            deal_id: The deal's `deals.id`.
        """
        cursor = await self._conn.execute("SELECT * FROM deals WHERE id = ?", (deal_id,))
        row = await cursor.fetchone()
        return _row_to_deal(row) if row is not None else None

    async def for_player(self, player_id: int) -> Sequence[Deal]:
        """Return every deal for a player, oldest first.

        Args:
            player_id: The player whose deals to list.
        """
        cursor = await self._conn.execute(
            "SELECT * FROM deals WHERE player_id = ? ORDER BY occurred_at, id", (player_id,)
        )
        return [_row_to_deal(row) async for row in cursor]

    async def count(self) -> int:
        """Return the total number of deals — Э4's import-verification count."""
        cursor = await self._conn.execute("SELECT COUNT(*) AS n FROM deals")
        row = await cursor.fetchone()
        return int(row["n"]) if row is not None else 0


_INSERT_SQL = """
    INSERT INTO deals (
        player_id, occurred_at, occurred_at_kind, deal_type, amount, coins, xp,
        rank_at_deal, booster_at_deal, recorded_by, source, legacy_sheet_row, created_at
    ) VALUES (
        :player_id, :occurred_at, :occurred_at_kind, :deal_type, :amount, :coins, :xp,
        :rank_at_deal, :booster_at_deal, :recorded_by, :source, :legacy_sheet_row, :created_at
    )
"""


def _deal_to_params(deal: Deal) -> dict[str, object]:
    return {
        "player_id": deal.player_id,
        "occurred_at": deal.occurred_at.isoformat(),
        "occurred_at_kind": deal.occurred_at_kind.value,
        "deal_type": deal.deal_type.value,
        "amount": int(deal.amount),
        "coins": deal.coins,
        "xp": deal.xp,
        "rank_at_deal": deal.rank_at_deal,
        "booster_at_deal": int(deal.booster_at_deal),
        "recorded_by": deal.recorded_by,
        "source": deal.source.value,
        "legacy_sheet_row": deal.legacy_sheet_row,
        "created_at": deal.created_at.isoformat(),
    }


def _row_to_deal(row: aiosqlite.Row) -> Deal:
    return Deal(
        id=row["id"],
        player_id=row["player_id"],
        occurred_at=datetime.fromisoformat(row["occurred_at"]),
        occurred_at_kind=OccurredAtKind(row["occurred_at_kind"]),
        deal_type=DealType(row["deal_type"]),
        amount=Rub(row["amount"]),
        coins=row["coins"],
        xp=row["xp"],
        rank_at_deal=row["rank_at_deal"],
        booster_at_deal=bool(row["booster_at_deal"]),
        recorded_by=row["recorded_by"],
        source=DealSource(row["source"]),
        legacy_sheet_row=row["legacy_sheet_row"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )
