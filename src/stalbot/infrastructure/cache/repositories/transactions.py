"""SQLite-backed cache of the `Тикеты` block (PLAN.md §8.1).

Transactions only ever grow (rows are never deleted or renumbered), so
unlike `items`/`users` this repository has no `replace_all` — sync is
always incremental, `upsert_many` from the last known row onward (PLAN.md
§8.2).

The `transactions` table itself has no display-cased nick column (§8.1's
schema does not give it one — only `users.nick_display` carries it, sourced
from this same block's original-case `B` column during sync). Reads here
`LEFT JOIN users` to recover it, falling back to the normalized nick in the
rare case a transaction is cached before its user row.
"""

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

import aiosqlite

from stalbot.application.dto.log_entry import LogEntry
from stalbot.domain.entities.transaction import TransactionRecord
from stalbot.domain.enums import DealType
from stalbot.domain.nick import NormalizedNick

_SELECT_WITH_DISPLAY = """
    SELECT t.*, COALESCE(u.nick_display, t.nick_norm) AS nick_display
    FROM transactions t
    LEFT JOIN users u ON u.nick_norm = t.nick_norm
"""

_SELECT_NUMBERED_PAGE = """
    WITH numbered AS (
        SELECT t.*, COALESCE(u.nick_display, t.nick_norm) AS nick_display,
               u.discord_id AS discord_id,
               ROW_NUMBER() OVER (
                   PARTITION BY date(t.occurred_at) ORDER BY t.sheet_row
               ) AS day_number
        FROM transactions t
        LEFT JOIN users u ON u.nick_norm = t.nick_norm
    )
    SELECT * FROM numbered ORDER BY occurred_at DESC, day_number DESC LIMIT ? OFFSET ?
"""


class TransactionsCacheRepository:
    """Read-mostly cache of `TransactionRecord`s, appended to by `CacheSync`."""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        """Wrap an already-open cache connection.

        Args:
            connection: Connection returned by `CacheDb.connect()`.
        """
        self._conn = connection

    async def last_row(self) -> int:
        """Return the highest cached `sheet_row`, or `0` if the cache is empty."""
        cursor = await self._conn.execute("SELECT MAX(sheet_row) AS last_row FROM transactions")
        row = await cursor.fetchone()
        value = row["last_row"] if row is not None else None
        return int(value) if value is not None else 0

    async def list_by_period(self, start: datetime, end: datetime) -> Sequence[TransactionRecord]:
        """Return every transaction whose `occurred_at` falls within `[start, end]`.

        Args:
            start: Inclusive lower bound (tz-aware).
            end: Inclusive upper bound (tz-aware).
        """
        cursor = await self._conn.execute(
            f"{_SELECT_WITH_DISPLAY} WHERE t.occurred_at BETWEEN ? AND ? ORDER BY t.sheet_row",
            (start.isoformat(), end.isoformat()),
        )
        return [_row_to_record(row) async for row in cursor]

    async def list_by_nick(self, nick: NormalizedNick) -> Sequence[TransactionRecord]:
        """Return every transaction recorded for a nick, oldest first.

        Args:
            nick: Normalized nick.
        """
        cursor = await self._conn.execute(
            f"{_SELECT_WITH_DISPLAY} WHERE t.nick_norm = ? ORDER BY t.sheet_row", (nick,)
        )
        return [_row_to_record(row) async for row in cursor]

    async def list_referral_targets(self, referrer: NormalizedNick) -> Sequence[NormalizedNick]:
        """Return the nicks of every player this referrer brought in.

        `referrer_norm` is only ever written on a referred player's very
        first transaction (PLAN.md §7.4, §10.12), so a distinct scan gives
        each referral exactly once regardless of how many deals followed.

        Args:
            referrer: Normalized nick of the player who did the referring.
        """
        cursor = await self._conn.execute(
            "SELECT DISTINCT nick_norm FROM transactions"
            " WHERE referrer_norm = ? ORDER BY nick_norm",
            (referrer,),
        )
        return [NormalizedNick(row["nick_norm"]) async for row in cursor]

    async def count_all(self) -> int:
        """Return the total number of cached transactions (`/logs` pagination, PLAN.md §10.10)."""
        cursor = await self._conn.execute("SELECT COUNT(*) AS n FROM transactions")
        row = await cursor.fetchone()
        return int(row["n"]) if row is not None else 0

    async def list_numbered_page(self, *, offset: int, limit: int) -> Sequence[LogEntry]:
        """Return one page of `/logs`, newest first, numbered within each day.

        `day_number` is computed by a window function over the whole table
        before pagination is applied, so it stays correct no matter which
        page is requested — the first deal after `00:00 GMT+3` is always
        `#1`, regardless of which page it lands on (PLAN.md §10.10).

        Args:
            offset: Rows to skip, newest-first.
            limit: Maximum rows to return.
        """
        cursor = await self._conn.execute(_SELECT_NUMBERED_PAGE, (limit, offset))
        return [_row_to_log_entry(row) async for row in cursor]

    async def get_by_row(self, sheet_row: int) -> TransactionRecord | None:
        """Look up a single transaction by its sheet row.

        Used to replay an idempotent write: on a repeated `idempotency_key`,
        `TransactionService` reconstructs the original response from here
        instead of writing again.

        Args:
            sheet_row: The row to look up.
        """
        cursor = await self._conn.execute(
            f"{_SELECT_WITH_DISPLAY} WHERE t.sheet_row = ?", (sheet_row,)
        )
        row = await cursor.fetchone()
        return _row_to_record(row) if row is not None else None

    async def upsert_many(self, records: Sequence[TransactionRecord]) -> None:
        """Insert or update transaction rows (full or incremental sync).

        Args:
            records: Rows read from the `Тикеты` block.
        """
        await self._conn.executemany(
            """
            INSERT INTO transactions (sheet_row, occurred_at, nick_norm, deal_type,
                                       amount, coins, xp, referrer_norm)
            VALUES (:sheet_row, :occurred_at, :nick_norm, :deal_type,
                    :amount, :coins, :xp, :referrer_norm)
            ON CONFLICT (sheet_row) DO UPDATE SET
                occurred_at = excluded.occurred_at,
                nick_norm = excluded.nick_norm,
                deal_type = excluded.deal_type,
                amount = excluded.amount,
                coins = excluded.coins,
                xp = excluded.xp,
                referrer_norm = excluded.referrer_norm
            """,
            [_record_to_params(record) for record in records],
        )
        await self._conn.commit()


def _record_to_params(record: TransactionRecord) -> dict[str, object]:
    return {
        "sheet_row": record.row,
        "occurred_at": record.at.isoformat(),
        "nick_norm": record.nick,
        "deal_type": record.deal_type.value,
        "amount": str(record.amount),
        "coins": record.coins,
        "xp": record.xp,
        "referrer_norm": record.referrer,
    }


def _row_to_log_entry(row: aiosqlite.Row) -> LogEntry:
    return LogEntry(
        day_number=row["day_number"], transaction=_row_to_record(row), discord_id=row["discord_id"]
    )


def _row_to_record(row: aiosqlite.Row) -> TransactionRecord:
    return TransactionRecord(
        row=row["sheet_row"],
        at=datetime.fromisoformat(row["occurred_at"]),
        nick=NormalizedNick(row["nick_norm"]),
        nick_display=row["nick_display"],
        deal_type=DealType(row["deal_type"]),
        amount=Decimal(row["amount"]),
        coins=row["coins"] or 0,
        xp=row["xp"] or 0,
        referrer=NormalizedNick(row["referrer_norm"]) if row["referrer_norm"] is not None else None,
    )
