"""`SyncPricesReport` — outcome of `/sync_prices` (PLAN.md §10.8)."""

from dataclasses import dataclass, field

from stalbot.application.dto.price_change import PriceChange


@dataclass(frozen=True, slots=True)
class SyncPricesReport:
    """What one `/sync_prices` run found across every `SYNC_LAYOUTS` sheet."""

    updated: tuple[PriceChange, ...] = field(default_factory=tuple)
    not_found: tuple[str, ...] = field(default_factory=tuple)
    """Names read from a price sheet that matched no catalog item — nothing
    to source a price from, so that row's price cell is left untouched."""
    unchanged_count: int = 0
    unparseable: tuple[str, ...] = field(default_factory=tuple)
    """Names whose price sheet cell had non-empty content that failed to
    parse as a number (garbage, `#REF!`, locale-formatted text, ...) before
    this sync overwrote it with the item database's real price (UX #7 — the
    item database is the source of truth, so unlike the pre-reversal
    behavior this is informational, not a reason to skip the write)."""
