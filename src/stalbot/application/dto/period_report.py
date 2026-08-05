"""DTOs returned by `StatsService.report()` (PLAN.md §10.11)."""

from dataclasses import dataclass
from decimal import Decimal

from stalbot.domain.clock import DateRange
from stalbot.domain.entities.transaction import TransactionRecord


@dataclass(frozen=True, slots=True)
class PlayerPeriodStats:
    """One player's deal turnover within a reported period."""

    nick_display: str
    discord_id: int | None
    purchases: Decimal
    """Σ покупок (у меня) — deals the bot bought from this player."""
    sales: Decimal
    """Σ продаж (мне) — deals the bot sold to this player."""

    @property
    def turnover(self) -> Decimal:
        """`purchases + sales` — the sort key for the player list (PLAN.md §10.11)."""
        return self.purchases + self.sales


@dataclass(frozen=True, slots=True)
class PeriodReport:
    """Aggregated deal statistics for a `DateRange` (PLAN.md §10.11).

    `players` is sorted by `turnover` descending, as `/day`/`/week`/`/month`
    display it.
    """

    period: DateRange
    players: tuple[PlayerPeriodStats, ...]
    deal_count: int
    deals: tuple[TransactionRecord, ...] = ()
    """Every individual deal in the period, oldest first (PLAN.md §10.11, UX #11)."""

    @property
    def total_purchases(self) -> Decimal:
        """Σ покупок (у меня) across every player in the period."""
        return sum((p.purchases for p in self.players), Decimal(0))

    @property
    def total_sales(self) -> Decimal:
        """Σ продаж (мне) across every player in the period."""
        return sum((p.sales for p in self.players), Decimal(0))

    @property
    def net_profit(self) -> Decimal:
        """`Σ покупок (у меня) − Σ продаж (мне)` (decision confirmed, PLAN.md §10.11)."""
        return self.total_purchases - self.total_sales
