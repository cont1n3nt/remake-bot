"""One-time importer: `sheet_snapshot` CSVs (Э0) -> trading schema (Э3).

sqlite_migration.md §VII (Э4). Lives in `scripts/`, not `src/` — one-shot
migration code should not sit forever in the coverage denominator (§XI).

What this does, in order:

1. Load `tickets.csv`/`users.csv`/`items.csv` from an Э0 snapshot directory.
2. Create a `players` row for every nick in `B ∪ H` (§I.10: some referrers
   are only ever mentioned in `H`, never have their own `B` row — they
   still need to exist as players to make `referrer_player_id` a real FK).
3. Import every real (non-placeholder) ticket row as a `deals` row.
   `occurred_at` for the 26.06–27.07.2026 backlog that never had a real
   date is evenly interpolated (§I.3, owner-confirmed order/lower-bound);
   everything from the first real date onward uses that date as-is.
4. Resolve `referrer_player_id` for every player — variant B (§III.2,
   owner-approved): the first non-blank `H` among a player's own deals,
   not the legacy formula's row-position bug.
5. Import `catalog_items` from `items.csv`, with the owner-approved
   corrections from §I.10 applied (typo fixes, `:katalik:` emoji, and
   `Аминокислота` soft-deleted rather than imported active).
6. Run a full `ProgressionRepository.recompute()` so every player's
   Coins/XP/rank/referral-role is materialized immediately after import.

`_to_bool`/`_to_decimal`/`_to_int_strict` below were originally shared with
the now-removed `infrastructure/cache/sync.py` (Э9); they live here now,
since this importer is the only thing left that needs them.
`_parse_ticket_row`'s "drop rows without a real date" behavior is exactly
the bug Э0/Э1 exist to fix, so this importer's date handling is new, not
reused (§VII).
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import math
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Final

from stalbot.domain.entities.catalog_item import CatalogItem
from stalbot.domain.entities.deal import Deal
from stalbot.domain.enums import DealSource, DealType, ItemCategory, OccurredAtKind
from stalbot.domain.errors import AmountParseError
from stalbot.domain.money import Rub, parse_amount, to_storage
from stalbot.domain.nick import NormalizedNick, normalize_nick
from stalbot.infrastructure.cache.db import CacheDb
from stalbot.infrastructure.cache.repositories.catalog_items import CatalogItemsRepository
from stalbot.infrastructure.cache.repositories.deals import DealsRepository
from stalbot.infrastructure.cache.repositories.items import normalize_item_name
from stalbot.infrastructure.cache.repositories.players import PlayersRepository
from stalbot.infrastructure.cache.repositories.progression import ProgressionRepository

logger = logging.getLogger(__name__)

_TRUTHY_STRINGS: Final = frozenset({"true", "1", "yes", "да", "истина"})


def _to_bool(value: object) -> bool:
    """Coerce a checkbox-column cell (`purchase`/`sale`/`is_booster`) to `bool`.

    A manually typed cell — e.g. the literal text `"FALSE"` — comes back as
    a non-empty string, and Python's own `bool("FALSE")` is `True`. Only a
    real `bool` or a recognized truthy string counts as `True`; every other
    string is `False`.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in _TRUTHY_STRINGS
    return bool(value)


def _to_decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        pass
    # A cell typed/pasted by hand can carry the same messy formatting
    # `parse_amount` already tolerates for Discord input (thousands-separator
    # spaces, a trailing "₽") — fall back to it before giving up, instead of
    # silently dropping the whole row.
    try:
        return parse_amount(str(value))
    except AmountParseError:
        return None


def _to_int_strict(value: object) -> int | None:
    """Coerce a cell to `int`, returning `None` if a non-empty value fails to parse.

    Used only for `coins`/`xp`: those are financially meaningful values, and
    an "anything unparseable is 0" fallback would make genuine data
    corruption indistinguishable from a real zero everywhere downstream. An
    empty cell is still `0`.
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


#: Owner-confirmed lower bound for the interpolated backlog (§I.3): the
#: 26.06.2026-27.07.2026 deals are in row order, but none has a real
#: recorded date, and this is when the backlog is known to start.
_INTERPOLATION_START: Final = datetime(2026, 6, 26, 0, 0, tzinfo=UTC)
_DATE_FORMAT: Final = "%d.%m.%y %H:%M"
#: Manually typed `Дата` cells don't all follow the sheet's own `Д.М.ГГ
#: ЧЧ:ММ` convention — some carry no time, some a 4-digit year, some
#: seconds. Tried in order; the first one that parses wins.
_DATE_FORMAT_FALLBACKS: Final[tuple[str, ...]] = (
    _DATE_FORMAT,
    "%d.%m.%y",
    "%d.%m.%Y %H:%M:%S",
    "%d.%m.%Y %H:%M",
    "%d.%m.%Y",
)


def _parse_ticket_date(text: str) -> datetime:
    """Parse a `Дата` cell, tolerating the hand-typed format variants above."""
    for fmt in _DATE_FORMAT_FALLBACKS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    raise ValueError(f"time data {text!r} does not match any known ticket date format")


#: §I.10 — opечатки исправляются at import; the bridge to убежка is by
#: `shelter_item_id` (Э5), not by name, so renaming here doesn't break it.
_NAME_CORRECTIONS: Final[dict[str, str]] = {
    "Термичкеская смесь": "Термическая смесь",
    "Набор тонких сверлел и бит": "Набор тонких сверл и бит",
    "Канистра с дизилем": "Канистра с дизелем",
}
_EMOJI_CORRECTIONS: Final[dict[str, str]] = {":katalik": ":katalik:"}
#: §I.10 — the owner confirmed this item isn't needed anywhere; imported
#: soft-deleted (not skipped) so its history/id still exists if ever needed.
_DELETED_ITEM_NAMES: Final[frozenset[str]] = frozenset({"Аминокислота"})

#: §I.7 — only the first two groups of "БУСТЫ" have an unambiguous single
#: section in the live sheet's merged header cells (verified against
#: `СКУПКА.xlsx`, 2026-08-10); groups 3-4 share a merged "Медицина" header,
#: and groups 5-7 share one merged "Пиротехника, Боеприпасы и другое"
#: header that doesn't cleanly split into the three profession names — left
#: unset (`section=None`) rather than guessed. Resource items have no
#: section data anywhere in the sheet. Full section coverage arrives with
#: the Э5 shelter bridge (`shelter_item_id`), which has one profession per
#: item unambiguously.
_BOOST_GROUP_SECTIONS: Final[dict[int, str]] = {
    1: "Кулинария",
    2: "Самогоноварение",
    3: "Медицина",
    4: "Медицина",
}


@dataclass(frozen=True, slots=True)
class _TicketRow:
    sheet_row: int
    nick_norm: NormalizedNick
    nick_display: str
    date_text: str
    deal_type: DealType | None
    amount: int
    coins: int
    xp: int
    referred_by_norm: NormalizedNick | None
    referred_by_display: str | None


def _load_tickets(path: Path) -> list[_TicketRow]:
    rows: list[_TicketRow] = []
    with path.open(encoding="utf-8", newline="") as f:
        for raw in csv.DictReader(f):
            is_purchase = _to_bool(raw["Покупка"])
            is_sale = _to_bool(raw["Продажа"])
            deal_type = DealType.PURCHASE if is_purchase else DealType.SALE if is_sale else None
            amount = _to_decimal(raw["Сумма"]) or Decimal(0)
            nick_raw = raw["Ник"].strip()
            referred_by_raw = raw["Пришел от:"].strip()
            rows.append(
                _TicketRow(
                    sheet_row=int(raw["sheet_row"]),
                    nick_norm=normalize_nick(nick_raw),
                    nick_display=nick_raw,
                    date_text=raw["Дата"].strip(),
                    deal_type=deal_type,
                    amount=int(to_storage(amount)) if amount else 0,
                    coins=_to_int_strict(raw["Coins"]) or 0,
                    xp=_to_int_strict(raw["XP"]) or 0,
                    referred_by_norm=(normalize_nick(referred_by_raw) if referred_by_raw else None),
                    referred_by_display=referred_by_raw or None,
                )
            )
    return rows


def _resolve_display_names(tickets: Iterable[_TicketRow]) -> dict[NormalizedNick, str]:
    """`nick_norm -> first-seen original-case spelling`, from `B` first, then `H`."""
    display: dict[NormalizedNick, str] = {}
    for row in tickets:
        display.setdefault(row.nick_norm, row.nick_display)
    for row in tickets:
        if row.referred_by_norm is not None:
            display.setdefault(row.referred_by_norm, row.referred_by_display or "")
    return display


def _assign_occurred_at(tickets: list[_TicketRow]) -> dict[int, tuple[datetime, OccurredAtKind]]:
    """Compute `occurred_at`/`occurred_at_kind` per sheet_row (§I.3).

    Real deal rows only (placeholder rows never get a deal, so never need
    a date). Dated rows use their own date; dateless rows are evenly
    interpolated across `[_INTERPOLATION_START, first real date)`.
    """
    real_rows = [r for r in tickets if r.deal_type is not None]
    dated = sorted((r for r in real_rows if r.date_text), key=lambda r: r.sheet_row)
    if not dated:
        raise ValueError("no dated ticket rows found — cannot anchor the interpolation window")
    window_end = _parse_ticket_date(dated[0].date_text)

    undated = sorted((r for r in real_rows if not r.date_text), key=lambda r: r.sheet_row)
    interval = (window_end - _INTERPOLATION_START) / len(undated) if undated else None

    result: dict[int, tuple[datetime, OccurredAtKind]] = {}
    for offset, row in enumerate(undated):
        assert interval is not None  # noqa: S101 - undated is non-empty in this branch
        result[row.sheet_row] = (
            _INTERPOLATION_START + interval * offset,
            OccurredAtKind.SHEET_INTERPOLATED,
        )
    for row in real_rows:
        if row.date_text:
            occurred_at = _parse_ticket_date(row.date_text)
            result[row.sheet_row] = (occurred_at, OccurredAtKind.SHEET_TEXT)
    return result


@dataclass(frozen=True, slots=True)
class ImportReport:
    """Summary counts printed after a run — the plan's Э4 readiness checklist."""

    players_created: int
    deals_imported: int
    placeholder_rows_skipped: int
    referrers_resolved: int
    items_imported: int
    items_deleted: int
    items_without_section: int
    recomputed_players: int


async def run(snapshot_dir: Path, cache_db: CacheDb, *, now: datetime) -> ImportReport:
    """Run the full import against an already-connected `CacheDb`.

    Args:
        snapshot_dir: An Э0 snapshot directory (e.g. `tests/fixtures/sheet_snapshot_2026-08-10`).
        cache_db: A `CacheDb` — `connect()` may or may not have been called yet.
        now: Timestamp stamped on every created/updated row.
    """
    connection = await cache_db.connect()
    tickets = _load_tickets(snapshot_dir / "tickets.csv")

    players_repo = PlayersRepository(connection)
    deals_repo = DealsRepository(connection)
    items_repo = CatalogItemsRepository(connection)
    progression_repo = ProgressionRepository(connection)

    # --- players: union of B and H (§I.10) ---
    display_names = _resolve_display_names(tickets)
    player_ids: dict[NormalizedNick, int] = {}
    for nick_norm, display in display_names.items():
        player = await players_repo.get_or_create(nick_norm, display or nick_norm, now=now)
        assert player.id is not None  # noqa: S101 - just created/fetched
        player_ids[nick_norm] = player.id

    # --- deals ---
    occurred_at_by_row = _assign_occurred_at(tickets)
    deals: list[Deal] = []
    placeholder_count = 0
    for row in tickets:
        if row.deal_type is None:
            placeholder_count += 1
            continue
        occurred_at, kind = occurred_at_by_row[row.sheet_row]
        deals.append(
            Deal(
                id=None,
                player_id=player_ids[row.nick_norm],
                occurred_at=occurred_at,
                occurred_at_kind=kind,
                deal_type=row.deal_type,
                amount=Rub(row.amount),  # already converted through to_storage() in _load_tickets
                coins=row.coins,
                xp=row.xp,
                # No historical booster/Prestige+ player exists yet in the
                # live data (§XIV.1) — a real point-in-time snapshot would
                # require replaying every deal in order, which isn't worth
                # building for a fact pattern that's uniformly "none" today.
                rank_at_deal=None,
                booster_at_deal=False,
                recorded_by=None,
                source=DealSource.IMPORT,
                legacy_sheet_row=row.sheet_row,
                created_at=now,
            )
        )
    await deals_repo.insert_many(deals)

    # --- referrer resolution: variant B (§III.2) ---
    # Deliberately includes placeholder rows, not just real deals: §I.10's
    # row 544 ("Scaryyyyy", no deal, H="Nikobaby") exists *specifically* to
    # record a referral for a player with nothing else to attach it to —
    # excluding placeholders here would silently drop exactly the rows the
    # sheet invented this convention to capture.
    by_nick: dict[NormalizedNick, list[_TicketRow]] = defaultdict(list)
    for row in tickets:
        by_nick[row.nick_norm].append(row)
    for rows in by_nick.values():
        rows.sort(key=lambda r: r.sheet_row)

    referrers_resolved = 0
    for nick_norm, own_rows in by_nick.items():
        referrer_norm = next(
            (r.referred_by_norm for r in own_rows if r.referred_by_norm is not None), None
        )
        if referrer_norm is None:
            continue
        referrer_id = player_ids.get(referrer_norm)
        if referrer_id is None:
            continue
        await players_repo.set_referrer(player_ids[nick_norm], referrer_id, now=now)
        referrers_resolved += 1

    # --- catalog items ---
    items, deleted_count, no_section_count = _load_items(snapshot_dir / "items.csv", now=now)
    await items_repo.insert_many(items)

    # --- materialize progression ---
    recomputed = await progression_repo.recompute(now=now)

    return ImportReport(
        players_created=len(player_ids),
        deals_imported=len(deals),
        placeholder_rows_skipped=placeholder_count,
        referrers_resolved=referrers_resolved,
        items_imported=len(items) - deleted_count,
        items_deleted=deleted_count,
        items_without_section=no_section_count,
        recomputed_players=len(recomputed),
    )


def _load_items(path: Path, *, now: datetime) -> tuple[list[CatalogItem], int, int]:
    boost_sections = _load_boost_sections(path.with_name("boosts_top.csv"))
    items: list[CatalogItem] = []
    deleted_count = 0
    no_section_count = 0
    with path.open(encoding="utf-8", newline="") as f:
        for raw in csv.DictReader(f):
            name = _NAME_CORRECTIONS.get(raw["item name"], raw["item name"]).strip()
            category = ItemCategory(raw["category"])
            emoji = raw["emoji"] or None
            if emoji is not None:
                emoji = _EMOJI_CORRECTIONS.get(emoji, emoji)
            price_buy = _to_decimal(raw["price_buy"])
            price_sell = _to_decimal(raw["price_sell"])
            is_deleted = name in _DELETED_ITEM_NAMES
            deleted_count += int(is_deleted)
            section = (
                boost_sections.get(normalize_item_name(name))
                if category is ItemCategory.BOOST
                else None
            )
            no_section_count += int(category is ItemCategory.BOOST and section is None)
            items.append(
                CatalogItem(
                    id=None,
                    name=name,
                    name_norm=normalize_item_name(name),
                    category=category,
                    section=section,
                    price_buy=to_storage(price_buy) if price_buy is not None else None,
                    price_sell=to_storage(price_sell) if price_sell is not None else None,
                    emoji=emoji,
                    sort_order=int(raw["sheet_row"]),
                    shelter_item_id=None,
                    created_at=now,
                    updated_at=None,
                    deleted_at=now if is_deleted else None,
                )
            )
    return items, deleted_count, no_section_count


def _load_boost_sections(path: Path) -> dict[str, str]:
    """`name_norm -> section`, from `boosts_top.csv`'s group column (§I.7)."""
    sections: dict[str, str] = {}
    if not path.exists():
        return sections
    with path.open(encoding="utf-8", newline="") as f:
        for raw in csv.DictReader(f):
            section = _BOOST_GROUP_SECTIONS.get(int(raw["group"]))
            name = raw["name"].strip()
            if section is not None and name:
                sections[normalize_item_name(name)] = section
    return sections


async def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-dir", type=Path, required=True)
    parser.add_argument("--db-path", type=Path, required=True)
    args = parser.parse_args(argv)

    cache_db = CacheDb(args.db_path)
    report = await run(args.snapshot_dir, cache_db, now=datetime.now(UTC))
    await cache_db.close()

    logger.info("Игроков создано: %d", report.players_created)
    logger.info(
        "Сделок импортировано: %d (пропущено заготовок: %d)",
        report.deals_imported,
        report.placeholder_rows_skipped,
    )
    logger.info("Рефереров разрешено: %d", report.referrers_resolved)
    logger.info(
        "Предметов импортировано: %d (удалено мягко: %d, без секции: %d)",
        report.items_imported,
        report.items_deleted,
        report.items_without_section,
    )
    logger.info("Прогрессия пересчитана для %d игроков", report.recomputed_players)


if __name__ == "__main__":
    asyncio.run(main())
