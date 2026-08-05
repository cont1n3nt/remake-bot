"""`/day`, `/week`, `/month` — deal statistics by period (PLAN.md §10.11).

One `report()` shared by all three; each command is a thin wrapper supplying
a different `DateRange` factory. `/logs` (the plain paginated archive, not a
period aggregate) reads `TransactionsCacheRepository.list_numbered_page()`
directly instead — there is nothing for this service to aggregate there.
"""

from datetime import datetime, time
from decimal import Decimal

from stalbot.application.dto.period_report import PeriodReport, PlayerPeriodStats
from stalbot.domain.clock import GMT3, DateRange
from stalbot.domain.enums import DealType
from stalbot.domain.nick import NormalizedNick
from stalbot.infrastructure.cache.repositories.transactions import TransactionsCacheRepository
from stalbot.infrastructure.cache.repositories.users import UsersCacheRepository


class StatsService:
    """Aggregates cached transactions into a `PeriodReport`."""

    def __init__(
        self, transactions: TransactionsCacheRepository, users: UsersCacheRepository
    ) -> None:
        """Wire the service to its collaborators.

        Args:
            transactions: Source of deals for the requested period.
            users: Resolves each player's bound Discord id for display.
        """
        self._transactions = transactions
        self._users = users

    async def report(self, period: DateRange) -> PeriodReport:
        """Aggregate every deal within *period*, grouped by player (PLAN.md §10.11).

        Args:
            period: Inclusive date range, in `GMT3` calendar days.
        """
        start = datetime.combine(period.start, time.min, tzinfo=GMT3)
        end = datetime.combine(period.end, time.max, tzinfo=GMT3)
        records = await self._transactions.list_by_period(start, end)

        purchases: dict[NormalizedNick, Decimal] = {}
        sales: dict[NormalizedNick, Decimal] = {}
        displays: dict[NormalizedNick, str] = {}
        order: list[NormalizedNick] = []
        for record in records:
            if record.nick not in displays:
                order.append(record.nick)
                displays[record.nick] = record.nick_display
                purchases[record.nick] = Decimal(0)
                sales[record.nick] = Decimal(0)
            if record.deal_type is DealType.PURCHASE:
                purchases[record.nick] += record.amount
            else:
                sales[record.nick] += record.amount

        # One batched lookup instead of one `get_by_nick` per distinct player (APP-6).
        profiles = await self._users.get_by_nicks(order)
        players: list[PlayerPeriodStats] = []
        for nick in order:
            profile = profiles.get(nick)
            players.append(
                PlayerPeriodStats(
                    nick_display=displays[nick],
                    discord_id=profile.discord_id if profile is not None else None,
                    purchases=purchases[nick],
                    sales=sales[nick],
                )
            )
        players.sort(key=lambda p: p.turnover, reverse=True)

        return PeriodReport(period=period, players=tuple(players), deal_count=len(records))
