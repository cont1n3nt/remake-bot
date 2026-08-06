"""Sheets -> SQLite cache sync (see PLAN.md §8.2, §7.3).

Two independent cycles, matching the plan's refresh cadence:

- `sync_users_and_transactions` — Тикеты + Юзеры blocks, plus the `F`/`G`
  formula-extent check (§7.3 wants that checked "on every sync"; folding it
  into this cycle costs one extra read here rather than on every cycle).
- `sync_items` — the item database block.

Each cycle is one or two `SheetsClient` calls, so the periodic
`tasks.loop` wiring (M2's other half, in `presentation/bot.py`) stays
comfortably under the "≤2 API requests per cycle" DoD. `run_startup_sync`
additionally validates the sheet's structure (titles + headers) before
either cycle runs, and is the only place that does — re-validating on every
three-minute tick would just burn quota for a check that only matters after
a human edits the sheet.
"""

import logging
import math
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Final

from stalbot.application.ports.clock import Clock
from stalbot.domain.clock import parse_sheet_datetime
from stalbot.domain.entities.item import Item
from stalbot.domain.entities.transaction import TransactionRecord
from stalbot.domain.entities.user_profile import UserProfile
from stalbot.domain.enums import DealType, ItemCategory
from stalbot.domain.errors import AmountParseError
from stalbot.domain.money import parse_amount
from stalbot.domain.nick import NormalizedNick, normalize_nick
from stalbot.infrastructure.cache.repositories.items import ItemsCacheRepository
from stalbot.infrastructure.cache.repositories.transactions import TransactionsCacheRepository
from stalbot.infrastructure.cache.repositories.users import UsersCacheRepository
from stalbot.infrastructure.sheets.a1 import a1_range
from stalbot.infrastructure.sheets.client import CellGrid, SheetsClient
from stalbot.infrastructure.sheets.layouts import (
    DATA_START_ROW,
    DATABASE_SHEET,
    ITEMS_BLOCK,
    TICKETS_BLOCK,
    USERS_BLOCK,
)

logger = logging.getLogger(__name__)

_TICKETS_RANGE = a1_range(
    DATABASE_SHEET, TICKETS_BLOCK.col_start, DATA_START_ROW, TICKETS_BLOCK.col_end
)
_USERS_RANGE = a1_range(DATABASE_SHEET, USERS_BLOCK.col_start, DATA_START_ROW, USERS_BLOCK.col_end)
_ITEMS_RANGE = a1_range(DATABASE_SHEET, ITEMS_BLOCK.col_start, DATA_START_ROW, ITEMS_BLOCK.col_end)
_FORMULA_COLUMN_RANGE = a1_range(DATABASE_SHEET, "F", DATA_START_ROW, "F")

#: PLAN.md §7.3: warn once fewer than this many pre-extended formula rows remain.
LOW_FREE_ROWS_THRESHOLD = 50

_TICKETS_WIDTH = 8
_USERS_WIDTH = 11
_ITEMS_WIDTH = 7


@dataclass(frozen=True, slots=True)
class SyncReport:
    """Outcome of one sync cycle, logged and surfaced to `/healthcheck` (M11)."""

    items_synced: int = 0
    users_synced: int = 0
    transactions_synced: int = 0
    transactions_skipped: int = 0
    formula_free_rows: int | None = None
    api_calls: int = 0
    duration_seconds: float = 0.0
    warnings: tuple[str, ...] = field(default_factory=tuple)


class CacheSync:
    """Pulls the `DataBase` sheet into the SQLite cache."""

    def __init__(
        self,
        sheets: SheetsClient,
        *,
        items: ItemsCacheRepository,
        users: UsersCacheRepository,
        transactions: TransactionsCacheRepository,
        clock: Clock,
    ) -> None:
        """Wire the sync to its Sheets client and cache repositories.

        Args:
            sheets: Client to read the `DataBase` sheet through.
            items: Cache repository for the item database.
            users: Cache repository for the user base.
            transactions: Cache repository for the Тикеты block.
            clock: Time source for `synced_at` timestamps.
        """
        self._sheets = sheets
        self._items = items
        self._users = users
        self._transactions = transactions
        self._clock = clock
        #: Surfaced by `/healthcheck` and the per-minute metrics log (M11).
        self.last_users_report: SyncReport | None = None
        self.last_items_report: SyncReport | None = None
        self._fresh_reads = 0
        self._stale_refreshes = 0

    async def run_startup_sync(self) -> SyncReport:
        """Validate the sheet's structure, then run both sync cycles once.

        Raises:
            SheetStructureError: If a sheet is missing or headers mismatch.
        """
        started = time.monotonic()
        await self._sheets.validate_layout()
        users_report = await self.sync_users_and_transactions()
        items_report = await self.sync_items()
        report = SyncReport(
            items_synced=items_report.items_synced,
            users_synced=users_report.users_synced,
            transactions_synced=users_report.transactions_synced,
            transactions_skipped=users_report.transactions_skipped,
            formula_free_rows=users_report.formula_free_rows,
            api_calls=users_report.api_calls + items_report.api_calls,
            duration_seconds=time.monotonic() - started,
            warnings=users_report.warnings + items_report.warnings,
        )
        logger.info(
            "startup sync complete: %d items, %d users, %d transactions, %d API calls, %.2fs",
            report.items_synced,
            report.users_synced,
            report.transactions_synced,
            report.api_calls,
            report.duration_seconds,
        )
        return report

    async def sync_users_and_transactions(self) -> SyncReport:
        """Refresh the Юзеры and Тикеты caches, and check the formula extent."""
        started = time.monotonic()
        result = await self._sheets.batch_get([_TICKETS_RANGE, _USERS_RANGE])
        tickets_rows = result.get(_TICKETS_RANGE, [])
        users_rows = result.get(_USERS_RANGE, [])

        records, nick_displays, skipped = _parse_tickets(tickets_rows)
        profiles, user_warnings = _parse_users(users_rows)

        synced_at = self._clock.now().isoformat()
        await self._transactions.upsert_many(records)
        await self._users.replace_all(profiles, nick_displays=nick_displays, synced_at=synced_at)

        free_rows = await self._check_formula_extent(last_data_row=_last_row(tickets_rows))
        warnings = _free_rows_warning(free_rows) + user_warnings

        report = SyncReport(
            users_synced=len(profiles),
            transactions_synced=len(records),
            transactions_skipped=skipped,
            formula_free_rows=free_rows,
            api_calls=2,
            duration_seconds=time.monotonic() - started,
            warnings=warnings,
        )
        logger.info(
            "users sync: %d users, %d transactions (%d skipped), %d formula rows free, %.2fs",
            report.users_synced,
            report.transactions_synced,
            report.transactions_skipped,
            report.formula_free_rows,
            report.duration_seconds,
        )
        self.last_users_report = report
        return report

    async def sync_items(self) -> SyncReport:
        """Refresh the item database cache."""
        started = time.monotonic()
        result = await self._sheets.batch_get([_ITEMS_RANGE])
        items = parse_items_block(result.get(_ITEMS_RANGE, []))
        await self._items.replace_all(items)
        report = SyncReport(
            items_synced=len(items),
            api_calls=1,
            duration_seconds=time.monotonic() - started,
        )
        logger.info("items sync: %d items, %.2fs", report.items_synced, report.duration_seconds)
        self.last_items_report = report
        return report

    @property
    def cache_hit_rate(self) -> float | None:
        """Fraction of `ensure_fresh` calls that found the cache already fresh.

        `None` until `ensure_fresh` has been called at least once — reported
        by `/healthcheck` and the per-minute metrics log (PLAN.md §12, M11).
        """
        total = self._fresh_reads + self._stale_refreshes
        return self._fresh_reads / total if total > 0 else None

    async def ensure_fresh(self, *, max_age_seconds: float) -> bool:
        """Trigger an inline users/transactions refresh if the cache is stale.

        Read paths call this before answering (PLAN.md §8.2: "если данные
        старше `STALE_AFTER`, выполняется inline-рефреш перед ответом").

        Args:
            max_age_seconds: Maximum tolerable age of the cache (`STALE_AFTER`).

        Returns:
            `True` if a refresh was performed, `False` if the cache was
            already fresh enough.
        """
        last_synced = await self._users.last_synced_at()
        if last_synced is not None:
            age = (self._clock.now() - last_synced).total_seconds()
            if age <= max_age_seconds:
                self._fresh_reads += 1
                return False
        self._stale_refreshes += 1
        await self.sync_users_and_transactions()
        return True

    async def _check_formula_extent(self, *, last_data_row: int) -> int:
        formula_rows = await self._sheets.read_formula_extent(_FORMULA_COLUMN_RANGE)
        formula_last_row = (
            DATA_START_ROW + formula_rows - 1 if formula_rows > 0 else DATA_START_ROW - 1
        )
        return max(0, formula_last_row - last_data_row)


def _free_rows_warning(free_rows: int) -> tuple[str, ...]:
    if free_rows >= LOW_FREE_ROWS_THRESHOLD:
        return ()
    message = (
        f"⚠️ Формулы протянуты только на {free_rows} свободных строк "
        f"(порог {LOW_FREE_ROWS_THRESHOLD}). Продлите диапазоны формул F/G."
    )
    logger.warning(message)
    return (message,)


def _last_row(rows: CellGrid) -> int:
    return DATA_START_ROW + len(rows) - 1 if rows else DATA_START_ROW - 1


def _pad(row: Sequence[object], width: int) -> list[object]:
    values = list(row)
    return values + [""] * (width - len(values))


def _parse_tickets(
    rows: CellGrid,
) -> tuple[list[TransactionRecord], dict[NormalizedNick, str], int]:
    records: list[TransactionRecord] = []
    nick_displays: dict[NormalizedNick, str] = {}
    skipped = 0

    for offset, raw_row in enumerate(rows):
        sheet_row = DATA_START_ROW + offset
        date_raw, nick_raw, purchase, sale, amount_raw, coins_raw, xp_raw, referrer_raw = _pad(
            raw_row, _TICKETS_WIDTH
        )

        nick_text = str(nick_raw).strip()
        if not nick_text:
            continue
        nick_norm = normalize_nick(nick_text)
        nick_displays.setdefault(nick_norm, nick_text)

        record = _parse_ticket_row(
            sheet_row=sheet_row,
            date_raw=date_raw,
            nick_norm=nick_norm,
            purchase=_to_bool(purchase),
            sale=_to_bool(sale),
            amount_raw=amount_raw,
            coins_raw=coins_raw,
            xp_raw=xp_raw,
            referrer_raw=referrer_raw,
            nick_text=nick_text,
        )
        if record is None:
            skipped += 1
        else:
            records.append(record)

    return records, nick_displays, skipped


def _parse_ticket_row(
    *,
    sheet_row: int,
    date_raw: object,
    nick_norm: NormalizedNick,
    purchase: bool,
    sale: bool,
    amount_raw: object,
    coins_raw: object,
    xp_raw: object,
    referrer_raw: object,
    nick_text: str,
) -> TransactionRecord | None:
    at = parse_sheet_datetime(str(date_raw)) if date_raw else None
    if at is None:
        return None

    if purchase == sale:  # neither flag set, or (invalidly) both
        return None

    amount = _to_decimal(amount_raw)
    if amount is None:
        return None

    coins = _to_int_strict(coins_raw)
    xp = _to_int_strict(xp_raw)
    if coins is None or xp is None:
        # INFRA1-4: a non-empty, unparseable coins/xp cell is sheet
        # corruption, not "zero" — silently caching a false zero for a
        # financially meaningful field is worse than dropping the row.
        logger.warning(
            "Тикеты, строка %d (%s): нечисловое значение %s, строка пропущена",
            sheet_row,
            nick_text,
            "coins" if coins is None else "xp",
        )
        return None

    referrer_text = str(referrer_raw).strip() if referrer_raw else ""
    return TransactionRecord(
        row=sheet_row,
        at=at,
        nick=nick_norm,
        nick_display=nick_text,
        deal_type=DealType.PURCHASE if purchase else DealType.SALE,
        amount=amount,
        coins=coins,
        xp=xp,
        referrer=normalize_nick(referrer_text) if referrer_text else None,
    )


def _parse_users(rows: CellGrid) -> tuple[list[UserProfile], tuple[str, ...]]:
    profiles: list[UserProfile] = []
    warnings: list[str] = []
    for offset, raw_row in enumerate(rows):
        sheet_row = DATA_START_ROW + offset
        (
            discord_id_raw,
            nick_norm_raw,
            coins_raw,
            xp_raw,
            buy_raw,
            sell_raw,
            total_raw,
            referrals_raw,
            booster_raw,
            rank_raw,
            referral_role_raw,
        ) = _pad(raw_row, _USERS_WIDTH)

        nick_text = str(nick_norm_raw).strip()
        if not nick_text:
            continue

        # INFRA1-4: unlike a Тикеты row, dropping a whole profile over one
        # garbled cell (via the `replace_all` full-wipe that follows) would
        # erase turnover, rank, referral role and Discord binding too — a
        # worse outcome than a temporary false zero on just that one field.
        # So the profile is always kept; a corrupt coins/xp cell only ever
        # falls back to `0` (same as a genuinely empty cell), and is loudly
        # logged/reported rather than silently indistinguishable from one.
        coins_strict = _to_int_strict(coins_raw)
        xp_strict = _to_int_strict(xp_raw)
        if coins_strict is None or xp_strict is None:
            message = (
                f"⚠️ Юзеры, строка {sheet_row} ({nick_text}): нечисловое значение "
                f"{'coins' if coins_strict is None else 'xp'}, использовано временное значение 0"
            )
            logger.warning(message)
            warnings.append(message)
        coins = coins_strict if coins_strict is not None else 0
        xp = xp_strict if xp_strict is not None else 0

        profiles.append(
            UserProfile(
                row=sheet_row,
                nick=NormalizedNick(nick_text),
                discord_id=_to_int(discord_id_raw) if discord_id_raw else None,
                coins=coins,
                xp=xp,
                buy_turnover=_to_decimal(buy_raw) or Decimal(0),
                sell_turnover=_to_decimal(sell_raw) or Decimal(0),
                total_turnover=_to_decimal(total_raw) or Decimal(0),
                referrals_count=_to_int(referrals_raw),
                is_booster=_to_bool(booster_raw),
                rank=str(rank_raw).strip() or None,
                referral_role=str(referral_role_raw).strip() or None,
            )
        )
    return profiles, tuple(warnings)


def parse_items_block(rows: CellGrid) -> list[Item]:
    """Parse the item database block's raw cell grid into `Item`s.

    Public (not `_`-prefixed) because `CatalogService.delete_item` (M6)
    also needs to parse a fresh read of the same block to renumber it —
    duplicating this row-shape knowledge would be the kind of drift PLAN.md
    §1.3 rules out.

    Args:
        rows: Cell grid read from the `item database` block's A1 range.
    """
    items: list[Item] = []
    for offset, raw_row in enumerate(rows):
        sheet_row = DATA_START_ROW + offset
        id_raw, name_raw, category_raw, buy_raw, sell_raw, emoji_raw, updated_raw = _pad(
            raw_row, _ITEMS_WIDTH
        )

        name_text = str(name_raw).strip()
        id_text = str(id_raw).strip()
        category_text = str(category_raw).strip()
        if not name_text or not id_text or not category_text:
            continue
        item_id = _to_int(id_raw)

        try:
            category = ItemCategory(category_text)
        except ValueError:
            logger.warning(
                "item database row %d: unknown category %r, skipped", sheet_row, category_text
            )
            continue

        updated_text = str(updated_raw).strip()
        items.append(
            Item(
                id=item_id,
                name=name_text,
                category=category,
                price_buy=_to_decimal(buy_raw),
                price_sell=_to_decimal(sell_raw),
                emoji=str(emoji_raw).strip() or None,
                updated_at=parse_sheet_datetime(updated_text) if updated_text else None,
                row=sheet_row,
            )
        )
    return items


def _to_decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        pass
    # A cell typed/pasted by hand (rather than written by the bot) can carry
    # the same messy formatting `parse_amount` already tolerates for Discord
    # input (thousands-separator spaces, a trailing "₽") — fall back to it
    # before giving up, instead of silently dropping the whole row.
    try:
        return parse_amount(str(value))
    except AmountParseError:
        return None


def _to_int(value: object) -> int:
    if value is None or value == "":
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        # `int(nan)`/`int(inf)` raise (ValueError/OverflowError) rather than
        # returning something — treat a non-finite cell like any other
        # unparseable one instead of letting that escape uncaught.
        return int(value) if math.isfinite(value) else 0
    if isinstance(value, str):
        try:
            parsed = float(value)
        except ValueError:
            return 0
        return int(parsed) if math.isfinite(parsed) else 0
    return 0


def _to_int_strict(value: object) -> int | None:
    """Like `_to_int`, but a non-empty value that fails to parse is `None`.

    Used only for `coins`/`xp` (INFRA1-4): those are financially meaningful
    formula outputs, and `_to_int`'s "anything unparseable is 0" fallback
    would make genuine sheet corruption indistinguishable from a real zero
    everywhere downstream. An empty cell is still `0` — that is the normal
    state of a row whose formulas have not recalculated yet.
    """
    if value is None or value == "":
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) else None
    if isinstance(value, str):
        try:
            parsed = float(value)
        except ValueError:
            return None
        return int(parsed) if math.isfinite(parsed) else None
    return None


_TRUTHY_STRINGS: Final = frozenset({"true", "1", "yes", "да", "истина"})


def _to_bool(value: object) -> bool:
    """Coerce a checkbox-column cell (`purchase`/`sale`/`is_booster`) to `bool`.

    `UNFORMATTED_VALUE` normally returns a real `bool` for an actual
    checkbox cell, but a manually typed cell — e.g. the literal text
    `"FALSE"` — comes back as a non-empty string, and Python's own
    `bool("FALSE")` is `True` (INFRA1-5). Only a real `bool` or a
    recognized truthy string counts as `True`; every other string is `False`.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in _TRUTHY_STRINGS
    return bool(value)
