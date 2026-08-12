"""DTOs returned by `StatsService.report()` (PLAN.md §10.11; sqlite_migration.md Э6)."""

from dataclasses import dataclass

from stalbot.domain.clock import DateRange
from stalbot.domain.entities.deal import Deal
from stalbot.domain.money import Rub


@dataclass(frozen=True, slots=True)
class PlayerPeriodStats:
    """One player's deal turnover within a reported period."""

    nick_display: str
    discord_id: int | None
    purchases: Rub
    """Σ покупок (у меня) — deals the bot bought from this player."""
    sales: Rub
    """Σ продаж (мне) — deals the bot sold to this player."""

    @property
    def turnover(self) -> Rub:
        """`purchases + sales` — the sort key for the player list (PLAN.md §10.11)."""
        return Rub(self.purchases + self.sales)


@dataclass(frozen=True, slots=True)
class PeriodDeal:
    """One deal in a period's individual listing, with its display-cased nick.

    `Deal` itself carries only `player_id` (sqlite_migration.md §IV.1) — the
    period's own deal listing needs the nick alongside it, same reason
    `LogEntry` pairs a nick with a raw transaction/deal.
    """

    nick_display: str
    deal: Deal


@dataclass(frozen=True, slots=True)
class PeriodReport:
    """Aggregated deal statistics for a `DateRange` (PLAN.md §10.11).

    `players` is sorted by `turnover` descending, as `/day`/`/week`/`/month`
    display it.
    """

    period: DateRange
    players: tuple[PlayerPeriodStats, ...]
    deal_count: int
    deals: tuple[PeriodDeal, ...] = ()
    """Every individual deal in the period, oldest first (PLAN.md §10.11, UX #11)."""

    @property
    def total_purchases(self) -> Rub:
        """Σ покупок (у меня) across every player in the period."""
        return Rub(sum((p.purchases for p in self.players), 0))

    @property
    def total_sales(self) -> Rub:
        """Σ продаж (мне) across every player in the period."""
        return Rub(sum((p.sales for p in self.players), 0))

    @property
    def net_profit(self) -> Rub:
        """`Σ покупок (у меня) − Σ продаж (мне)` (decision confirmed, PLAN.md §10.11)."""
        return Rub(self.total_purchases - self.total_sales)
