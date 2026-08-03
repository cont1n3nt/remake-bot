"""`SyncPricesReport` — outcome of `/sync_prices` (PLAN.md §10.8)."""

from dataclasses import dataclass, field

from stalbot.application.dto.price_change import PriceChange


@dataclass(frozen=True, slots=True)
class SyncPricesReport:
    """What one `/sync_prices` run found across every `SYNC_LAYOUTS` sheet."""

    updated: tuple[PriceChange, ...] = field(default_factory=tuple)
    not_found: tuple[str, ...] = field(default_factory=tuple)
    """Names read from a price sheet that matched no catalog item."""
    unchanged_count: int = 0
