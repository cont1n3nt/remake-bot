"""Recording a deal in the `Тикеты` block (PLAN.md §7.4, §10.1).

`TransactionService.register()` is the one place that ever writes a new
`Тикеты` row — used directly by `/add` (M4) and, unchanged, by ticket
confirmation (M9). It never writes `F`/`G` (they are formulas, decision
A2) and never writes `H` past a player's first-ever transaction (the `P`
referral-count formula is a plain `COUNTIF($H:$H; nick)` — writing the
referrer on every deal would inflate it).
"""

import logging
from collections.abc import Mapping
from datetime import datetime

from stalbot.application.dto.transaction_request import (
    AddTransactionRequest,
    TransactionRegistrationResult,
)
from stalbot.application.ports.clock import Clock
from stalbot.application.services.binding import bind_discord
from stalbot.domain.clock import format_datetime
from stalbot.domain.entities.transaction import TransactionRecord
from stalbot.domain.enums import DealType
from stalbot.domain.errors import SheetsWriteConflictError
from stalbot.domain.nick import normalize_nick
from stalbot.infrastructure.cache.repositories.idempotency import IdempotencyRepository
from stalbot.infrastructure.cache.repositories.transactions import TransactionsCacheRepository
from stalbot.infrastructure.cache.repositories.users import UsersCacheRepository
from stalbot.infrastructure.cache.sync import CacheSync
from stalbot.infrastructure.sheets.a1 import a1_range
from stalbot.infrastructure.sheets.client import CellGrid, SheetsClient
from stalbot.infrastructure.sheets.layouts import DATA_START_ROW, DATABASE_SHEET

logger = logging.getLogger(__name__)

_FORMULA_COLUMNS: tuple[str, str] = ("F", "G")
_FORMULA_WAIT_ATTEMPTS = 3
_FORMULA_WAIT_DELAY_SECONDS = 0.7
_MAX_ROW_SEARCH_ATTEMPTS = 5


class TransactionService:
    """Writes a deal to `DataBase`, waits for the formulas, refreshes the cache."""

    def __init__(
        self,
        sheets: SheetsClient,
        transactions: TransactionsCacheRepository,
        users: UsersCacheRepository,
        idempotency: IdempotencyRepository,
        cache_sync: CacheSync,
        *,
        clock: Clock,
    ) -> None:
        """Wire the service to its collaborators.

        Args:
            sheets: Sheets access (writes are protected + RAW, PLAN.md §7.3).
            transactions: Cache repository for the `Тикеты` block.
            users: Cache repository for the `Юзеры` block (Discord binding).
            idempotency: Prevents a retried write from duplicating a row.
            cache_sync: Runs the point-refresh after a successful write.
            clock: Time source, tz-aware `GMT3`.
        """
        self._sheets = sheets
        self._transactions = transactions
        self._users = users
        self._idempotency = idempotency
        self._cache_sync = cache_sync
        self._clock = clock

    async def register(self, request: AddTransactionRequest) -> TransactionRegistrationResult:
        """Record one deal, idempotently.

        Args:
            request: What to write and who it is for.

        Returns:
            The recorded transaction plus what else the call did (bound a
            Discord id, or gave up waiting for the formulas).

        Raises:
            SheetsWriteConflictError: If no free row could be found, or the
                read-back verification of the raw columns did not match.
        """
        replayed = await self._replay_if_seen(request.idempotency_key)
        if replayed is not None:
            return replayed

        nick = normalize_nick(request.nick)
        referrer = normalize_nick(request.referrer_nick) if request.referrer_nick else None
        now = self._clock.now()

        target_row = await self._find_free_row()
        is_first_transaction = len(await self._transactions.list_by_nick(nick)) == 0
        write_referrer = is_first_transaction and request.referrer_nick is not None

        await self._write_raw_columns(request, target_row, now, write_referrer=write_referrer)
        coins, xp, formula_pending = await self._await_formulas(target_row)

        await self._idempotency.record(
            request.idempotency_key, target_row, created_at=now.isoformat()
        )

        record = TransactionRecord(
            row=target_row,
            at=now,
            nick=nick,
            nick_display=request.nick,
            deal_type=request.deal_type,
            amount=request.amount,
            coins=coins,
            xp=xp,
            referrer=referrer if write_referrer else None,
        )
        await self._transactions.upsert_many([record])

        await self._cache_sync.sync_users_and_transactions()
        discord_bound = await bind_discord(
            self._users,
            self._sheets,
            self._clock,
            nick,
            request.discord_id,
            force=request.force_rebind,
        )

        return TransactionRegistrationResult(
            record=record, discord_bound=discord_bound, formula_pending=formula_pending
        )

    async def _replay_if_seen(self, key: str) -> TransactionRegistrationResult | None:
        cached_row = await self._idempotency.get(key)
        if cached_row is None:
            return None
        cached_record = await self._transactions.get_by_row(cached_row)
        if cached_record is None:
            return None  # cache was cleared/never synced back; fall through and write again
        return TransactionRegistrationResult(
            record=cached_record, discord_bound=False, formula_pending=False
        )

    async def _find_free_row(self) -> int:
        candidate = max(await self._transactions.last_row() + 1, DATA_START_ROW)
        for _ in range(_MAX_ROW_SEARCH_ATTEMPTS):
            ref = a1_range(DATABASE_SHEET, "B", candidate)
            result = await self._sheets.batch_get([ref])
            cell = result.get(ref, [])
            if not cell or not cell[0] or not str(cell[0][0]).strip():
                return candidate
            candidate += 1
        raise SheetsWriteConflictError(
            f"no free row found in Тикеты after {_MAX_ROW_SEARCH_ATTEMPTS} attempts"
        )

    async def _write_raw_columns(
        self, request: AddTransactionRequest, row: int, now: datetime, *, write_referrer: bool
    ) -> None:
        values_range = a1_range(DATABASE_SHEET, "A", row, "E", row)
        data: dict[str, CellGrid] = {
            values_range: [
                [
                    format_datetime(now),
                    request.nick,
                    request.deal_type is DealType.PURCHASE,
                    request.deal_type is DealType.SALE,
                    int(request.amount),
                ]
            ]
        }
        if write_referrer:
            data[a1_range(DATABASE_SHEET, "H", row)] = [[request.referrer_nick]]
        await self._sheets.write_verified(data)

    async def _await_formulas(self, row: int) -> tuple[int, int, bool]:
        f_ref = a1_range(DATABASE_SHEET, "F", row)
        g_ref = a1_range(DATABASE_SHEET, "G", row)

        def is_ready(result: Mapping[str, CellGrid]) -> bool:
            return _cell_nonempty(result, f_ref) and _cell_nonempty(result, g_ref)

        result = await self._sheets.read_until(
            [f_ref, g_ref],
            is_ready=is_ready,
            attempts=_FORMULA_WAIT_ATTEMPTS,
            delay_seconds=_FORMULA_WAIT_DELAY_SECONDS,
        )
        if not is_ready(result):
            # Row is past the customer's pre-extended formula range (PLAN.md
            # §7.3): copy the formula down from the row above instead of
            # composing one, then give the recalculation one more chance.
            await self._extend_formulas(row)
            result = await self._sheets.read_until(
                [f_ref, g_ref],
                is_ready=is_ready,
                attempts=_FORMULA_WAIT_ATTEMPTS,
                delay_seconds=_FORMULA_WAIT_DELAY_SECONDS,
            )

        if is_ready(result):
            return _cell_int(result, f_ref), _cell_int(result, g_ref), False

        logger.warning("row %d: F/G formulas did not resolve even after copyPaste fallback", row)
        return 0, 0, True

    async def _extend_formulas(self, row: int) -> None:
        extent_ref = a1_range(DATABASE_SHEET, "F", DATA_START_ROW)
        extent = await self._sheets.read_formula_extent(extent_ref)
        source_row = DATA_START_ROW + extent - 1 if extent > 0 else DATA_START_ROW - 1
        if source_row < DATA_START_ROW:
            logger.warning("row %d: no prior formula row to copy from", row)
            return
        await self._sheets.copy_formula_down(
            DATABASE_SHEET, columns=_FORMULA_COLUMNS, source_row=source_row, target_row=row
        )


def _cell_nonempty(result: Mapping[str, CellGrid], ref: str) -> bool:
    rows = result.get(ref, [])
    return bool(rows) and bool(rows[0]) and str(rows[0][0]).strip() != ""


def _cell_int(result: Mapping[str, CellGrid], ref: str) -> int:
    try:
        return int(str(result[ref][0][0]))
    except (KeyError, IndexError, TypeError, ValueError):
        return 0
