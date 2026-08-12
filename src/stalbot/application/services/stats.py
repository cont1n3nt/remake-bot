"""`/day`, `/week`, `/month` — deal statistics by period (PLAN.md §10.11).

One `report()` shared by all three; each command is a thin wrapper supplying
a different `DateRange` factory. `/logs` (the plain paginated archive, not a
period aggregate) reads `DealsRepository.list_numbered_page()` directly
instead — there is nothing for this service to aggregate there.

sqlite_migration.md Э6: reads `deals`/`players` instead of the sheet-era
`transactions`/`users` cache. `deals.occurred_at` is always populated now
(§I.3's interpolation), so every deal — not just the 54/657 with a real
sheet date — participates in a period bucket; `occurred_at_kind` travels
through on each `PeriodDeal` so the presentation layer can mark an
interpolated one as approximate.
"""

from datetime import datetime, time

from stalbot.application.dto.period_report import PeriodDeal, PeriodReport, PlayerPeriodStats
from stalbot.domain.clock import GMT3, DateRange
from stalbot.domain.enums import DealType
from stalbot.domain.money import Rub
from stalbot.infrastructure.cache.repositories.deals import DealsRepository
from stalbot.infrastructure.cache.repositories.players import PlayersRepository


class StatsService:
    """Aggregates cached deals into a `PeriodReport`."""

    def __init__(self, deals: DealsRepository, players: PlayersRepository) -> None:
        """Wire the service to its collaborators.

        Args:
            deals: Source of deals for the requested period.
            players: Resolves each deal's player for display.
        """
        self._deals = deals
        self._players = players

    async def report(self, period: DateRange) -> PeriodReport:
        """Aggregate every deal within *period*, grouped by player (PLAN.md §10.11).

        Args:
            period: Inclusive date range, in `GMT3` calendar days.
        """
        start = datetime.combine(period.start, time.min, tzinfo=GMT3)
        end = datetime.combine(period.end, time.max, tzinfo=GMT3)
        deals = await self._deals.list_by_period(start, end)

        players_by_id = await self._players.get_by_ids(d.player_id for d in deals)

        purchases: dict[int, int] = {}
        sales: dict[int, int] = {}
        order: list[int] = []
        for deal in deals:
            if deal.player_id not in purchases:
                order.append(deal.player_id)
                purchases[deal.player_id] = 0
                sales[deal.player_id] = 0
            if deal.deal_type is DealType.PURCHASE:
                purchases[deal.player_id] += deal.amount
            else:
                sales[deal.player_id] += deal.amount

        players: list[PlayerPeriodStats] = []
        for player_id in order:
            player = players_by_id.get(player_id)
            players.append(
                PlayerPeriodStats(
                    nick_display=player.nick_display if player is not None else str(player_id),
                    discord_id=player.discord_id if player is not None else None,
                    purchases=Rub(purchases[player_id]),
                    sales=Rub(sales[player_id]),
                )
            )
        players.sort(key=lambda p: p.turnover, reverse=True)

        period_deals = tuple(
            PeriodDeal(
                nick_display=(
                    players_by_id[deal.player_id].nick_display
                    if deal.player_id in players_by_id
                    else str(deal.player_id)
                ),
                deal=deal,
            )
            for deal in deals
        )

        return PeriodReport(
            period=period, players=tuple(players), deal_count=len(deals), deals=period_deals
        )
