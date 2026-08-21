"""Typed loader for `tests/fixtures/sheet_snapshot_*/` CSV fixtures, plus the
legacy/fixed referral-resolution aggregate builders the parity tests need
(sqlite_migration.md §VI.1-§VI.2).

Shared by the SQLite-migration characterization tests (Э1's
`test_sheet_parity.py` today; Э5's shelter-cost parity test later) so the
CSV-parsing and Sheets text-comparison semantics — case-insensitive,
blank-means-zero — are defined exactly once.

The two aggregate builders below intentionally live in test support code,
not in `src/`: `aggregates_legacy()` reproduces a *bug* (the `SUMIF`
row-position mismatch, sqlite_migration.md §III.2), and no production code
should ever compute that on purpose.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from stalbot.domain.enums import DealType
from stalbot.domain.progression.calculator import PlayerAggregates

#: A booster's per-deal surcharge/big-deal thresholds (K3/L3 formulas,
#: mirrors `perks.BOOSTER_BIG_DEAL_THRESHOLDS` — duplicated here rather than
#: imported so this test-support module has no opinion on calculator wiring).
_BOOSTER_PURCHASE_THRESHOLD = 10_000_000
_BOOSTER_SALE_THRESHOLD = 25_000_000
_BIG_DEAL_50M = 50_000_000
_BIG_DEAL_100M = 100_000_000


def normalize_nick(nick: str) -> str:
    """Sheets text comparisons (`COUNTIF`/`SUMIF`/`VLOOKUP`) are case-insensitive."""
    return nick.strip().lower()


def _parse_bool(raw: str) -> bool:
    return raw == "True"


def _parse_amount(raw: str) -> int:
    """Parse a ruble amount cell — blank (placeholder rows, §I.10) means 0."""
    return int(float(raw)) if raw != "" else 0


def _parse_optional_int(raw: str) -> int | None:
    return int(float(raw)) if raw != "" else None


@dataclass(frozen=True, slots=True)
class TicketRow:
    """One row of `tickets.csv` (`DataBase!A:H`, §I.1)."""

    sheet_row: int
    nick_norm: str
    deal_type: DealType | None
    """`None` for placeholder rows with both `Покупка`/`Продажа` false (§I.10)."""
    amount: int
    referred_by_norm: str
    """Normalized `Пришел от:` text, `""` if blank."""


@dataclass(frozen=True, slots=True)
class UserRow:
    """One row of `users.csv` (`DataBase!I:S`, §I.1) — the sheet's own K/L/…/S."""

    sheet_row: int
    nick_norm: str
    coins: int
    xp: int
    purchase_turnover: int
    sale_turnover: int
    total_turnover: int
    referral_count: int
    is_booster: bool
    rank_label: str
    """`""` for a blank `Ранг` cell (below Standard)."""
    referral_role_label: str
    """`""` for a blank `Роль реферала` cell (no referrals)."""


def load_tickets(path: Path) -> list[TicketRow]:
    """Load `tickets.csv` into typed rows, one per sheet row (§VI.1)."""
    with path.open(encoding="utf-8", newline="") as f:
        rows = []
        for raw in csv.DictReader(f):
            is_purchase = _parse_bool(raw["Покупка"])
            is_sale = _parse_bool(raw["Продажа"])
            deal_type = DealType.PURCHASE if is_purchase else DealType.SALE if is_sale else None
            rows.append(
                TicketRow(
                    sheet_row=int(raw["sheet_row"]),
                    nick_norm=normalize_nick(raw["Ник"]),
                    deal_type=deal_type,
                    amount=_parse_amount(raw["Сумма"]),
                    referred_by_norm=normalize_nick(raw["Пришел от:"]),
                )
            )
        return rows


def load_users(path: Path) -> list[UserRow]:
    """Load `users.csv` into typed rows, one per sheet row (§VI.1)."""
    with path.open(encoding="utf-8", newline="") as f:
        rows = []
        for raw in csv.DictReader(f):
            rows.append(
                UserRow(
                    sheet_row=int(raw["sheet_row"]),
                    nick_norm=normalize_nick(raw["Уникальный ник"]),
                    coins=_parse_amount(raw["Всего Coins"]),
                    xp=_parse_amount(raw["Всего XP"]),
                    purchase_turnover=_parse_amount(raw["Оборот покупок"]),
                    sale_turnover=_parse_amount(raw["Оборот Продаж"]),
                    total_turnover=_parse_amount(raw["Общий оборот"]),
                    referral_count=_parse_optional_int(raw["Рефералы"]) or 0,
                    is_booster=_parse_bool(raw["Бустер сервера"]),
                    rank_label=raw["Ранг"],
                    referral_role_label=raw["Роль реферала"],
                )
            )
        return rows


@dataclass(frozen=True, slots=True)
class _OwnStats:
    purchase_turnover: int
    sale_turnover: int
    booster_big_deal_count: int
    deal_count_over_50m: int
    deal_count_over_100m: int


def _own_stats(rows: list[TicketRow]) -> _OwnStats:
    """Aggregate one player's own deals — identical under legacy and fixed
    (§III.2's bug is entirely in referrer *resolution*, not in a player's
    own turnover/big-deal counting)."""
    purchase = sum(r.amount for r in rows if r.deal_type is DealType.PURCHASE)
    sale = sum(r.amount for r in rows if r.deal_type is DealType.SALE)
    booster_big = sum(
        1
        for r in rows
        if (r.deal_type is DealType.PURCHASE and r.amount >= _BOOSTER_PURCHASE_THRESHOLD)
        or (r.deal_type is DealType.SALE and r.amount >= _BOOSTER_SALE_THRESHOLD)
    )
    over_50m = sum(1 for r in rows if r.deal_type is not None and r.amount >= _BIG_DEAL_50M)
    over_100m = sum(1 for r in rows if r.deal_type is not None and r.amount >= _BIG_DEAL_100M)
    return _OwnStats(purchase, sale, booster_big, over_50m, over_100m)


def _group_by_nick(tickets: list[TicketRow]) -> dict[str, list[TicketRow]]:
    by_nick: dict[str, list[TicketRow]] = defaultdict(list)
    for row in tickets:
        by_nick[row.nick_norm].append(row)
    for rows in by_nick.values():
        rows.sort(key=lambda r: r.sheet_row)
    return by_nick


def build_legacy_aggregates(
    tickets: list[TicketRow], users: list[UserRow]
) -> dict[str, PlayerAggregates]:
    """Reproduce the live K3/L3 formula's aggregation *exactly*, bug included.

    - `has_referrer`: `VLOOKUP` semantics — the first ticket row where `B`
      matches this player's nick, even if that row's `H` is blank.
    - `referral_count`: `COUNTIF($H$3:$H_last, nick)` — counts ticket
      *rows* naming this player, not distinct referred players (a friend's
      second deal double-counts).
    - `referee_total_turnover`: `SUMIF($H$3:$H_last, nick, $O$3:$O_last)` —
      the row-position bug (§III.2): sums `O` (Юзеры' `total_turnover`) at
      the *ticket* row's own row number, not at the actual referred
      player's Юзеры row.
    """
    by_nick = _group_by_nick(tickets)
    users_by_row = {u.sheet_row: u for u in users}
    users_by_nick = {u.nick_norm: u for u in users}

    result: dict[str, PlayerAggregates] = {}
    for user in users:
        own_rows = by_nick.get(user.nick_norm, [])
        stats = _own_stats(own_rows)
        has_referrer = bool(own_rows) and own_rows[0].referred_by_norm != ""
        referral_count = sum(1 for row in tickets if row.referred_by_norm == user.nick_norm)
        referee_total_turnover = sum(
            users_by_row[row.sheet_row].total_turnover
            for row in tickets
            if row.referred_by_norm == user.nick_norm and row.sheet_row in users_by_row
        )
        result[user.nick_norm] = PlayerAggregates(
            purchase_turnover=stats.purchase_turnover,
            sale_turnover=stats.sale_turnover,
            coin_ledger_delta=0,
            referral_count=referral_count,
            referee_total_turnover=referee_total_turnover,
            has_referrer=has_referrer,
            is_booster=users_by_nick[user.nick_norm].is_booster,
            booster_big_deal_count=stats.booster_big_deal_count,
            deal_count_over_50m=stats.deal_count_over_50m,
            deal_count_over_100m=stats.deal_count_over_100m,
        )
    return result


def build_fixed_aggregates(
    tickets: list[TicketRow], users: list[UserRow]
) -> dict[str, PlayerAggregates]:
    """Variant B (§III.2, owner-approved): referrer = the first *non-blank*
    `H` among a player's own deals, and referee turnover/count are computed
    from the real resolved referrer relationship — no row-position bug."""
    by_nick = _group_by_nick(tickets)
    users_by_nick = {u.nick_norm: u for u in users}
    own_stats_by_nick = {nick: _own_stats(rows) for nick, rows in by_nick.items()}

    referrer_by_nick: dict[str, str | None] = {}
    for nick, rows in by_nick.items():
        referrer_by_nick[nick] = next(
            (r.referred_by_norm for r in rows if r.referred_by_norm != ""), None
        )

    referees_of: dict[str, list[str]] = defaultdict(list)
    for nick, referrer in referrer_by_nick.items():
        if referrer is not None:
            referees_of[referrer].append(nick)

    result: dict[str, PlayerAggregates] = {}
    for user in users:
        nick = user.nick_norm
        stats = own_stats_by_nick.get(nick, _OwnStats(0, 0, 0, 0, 0))
        referees = referees_of.get(nick, [])
        referee_total_turnover = sum(
            own_stats_by_nick[r].purchase_turnover + own_stats_by_nick[r].sale_turnover
            for r in referees
            if r in own_stats_by_nick
        )
        result[nick] = PlayerAggregates(
            purchase_turnover=stats.purchase_turnover,
            sale_turnover=stats.sale_turnover,
            coin_ledger_delta=0,
            referral_count=len(referees),
            referee_total_turnover=referee_total_turnover,
            has_referrer=referrer_by_nick.get(nick) is not None,
            is_booster=users_by_nick[nick].is_booster,
            booster_big_deal_count=stats.booster_big_deal_count,
            deal_count_over_50m=stats.deal_count_over_50m,
            deal_count_over_100m=stats.deal_count_over_100m,
        )
    return result
