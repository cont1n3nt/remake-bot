"""Tests for `stalbot.application.services.stats.StatsService` (PLAN.md §10.11)."""

from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from stalbot.application.services.stats import StatsService
from stalbot.domain.clock import DateRange
from stalbot.domain.entities.transaction import TransactionRecord
from stalbot.domain.entities.user_profile import UserProfile
from stalbot.domain.enums import DealType
from stalbot.domain.nick import NormalizedNick


def _tx(nick: str, deal_type: DealType, amount: Decimal, **overrides: object) -> TransactionRecord:
    defaults: dict[str, object] = {
        "row": 3,
        "at": datetime(2026, 7, 31, 12, 0, tzinfo=UTC),
        "nick": NormalizedNick(nick),
        "nick_display": nick.capitalize(),
        "deal_type": deal_type,
        "amount": amount,
        "coins": 0,
        "xp": 0,
        "referrer": None,
    }
    defaults.update(overrides)
    return TransactionRecord(**defaults)  # type: ignore[arg-type]


def _profile(nick: str, discord_id: int | None) -> UserProfile:
    return UserProfile(
        row=3,
        nick=NormalizedNick(nick),
        discord_id=discord_id,
        coins=0,
        xp=0,
        buy_turnover=Decimal(0),
        sell_turnover=Decimal(0),
        total_turnover=Decimal(0),
        referrals_count=0,
        is_booster=False,
        rank=None,
        referral_role=None,
    )


def _service(
    records: list[TransactionRecord], profiles: dict[str, UserProfile] | None = None
) -> tuple[StatsService, MagicMock]:
    transactions = MagicMock()
    transactions.list_by_period = AsyncMock(return_value=records)
    users = MagicMock()
    users.get_by_nicks = AsyncMock(
        side_effect=lambda nicks: {n: (profiles or {})[n] for n in nicks if n in (profiles or {})}
    )
    return StatsService(transactions, users), transactions


async def test_report_aggregates_purchases_and_sales_per_player() -> None:
    records = [
        _tx("scaryyyyy", DealType.PURCHASE, Decimal(5_000_000)),
        _tx("scaryyyyy", DealType.PURCHASE, Decimal(1_000_000)),
        _tx("scaryyyyy", DealType.SALE, Decimal(500_000)),
    ]
    service, _tx_repo = _service(records, {"scaryyyyy": _profile("scaryyyyy", 111)})

    report = await service.report(DateRange.day(date(2026, 7, 31)))

    assert report.deal_count == 3
    assert len(report.players) == 1
    player = report.players[0]
    assert player.discord_id == 111
    assert player.purchases == Decimal(6_000_000)
    assert player.sales == Decimal(500_000)
    assert player.turnover == Decimal(6_500_000)


async def test_report_computes_totals_and_net_profit() -> None:
    records = [
        _tx("alice", DealType.PURCHASE, Decimal(28_500_000)),
        _tx("bob", DealType.SALE, Decimal(12_300_000)),
    ]
    service, _tx_repo = _service(
        records, {"alice": _profile("alice", 1), "bob": _profile("bob", 2)}
    )

    report = await service.report(DateRange.day(date(2026, 7, 31)))

    assert report.total_purchases == Decimal(28_500_000)
    assert report.total_sales == Decimal(12_300_000)
    assert report.net_profit == Decimal(16_200_000)


async def test_report_net_profit_can_be_negative() -> None:
    records = [
        _tx("alice", DealType.PURCHASE, Decimal(1_000_000)),
        _tx("alice", DealType.SALE, Decimal(5_000_000)),
    ]
    service, _tx_repo = _service(records, {"alice": _profile("alice", 1)})

    report = await service.report(DateRange.day(date(2026, 7, 31)))

    assert report.net_profit == Decimal(-4_000_000)


async def test_report_sorts_players_by_turnover_descending() -> None:
    records = [
        _tx("small", DealType.PURCHASE, Decimal(100)),
        _tx("big", DealType.PURCHASE, Decimal(10_000)),
        _tx("medium", DealType.PURCHASE, Decimal(1_000)),
    ]
    service, _tx_repo = _service(
        records,
        {
            "small": _profile("small", None),
            "big": _profile("big", None),
            "medium": _profile("medium", None),
        },
    )

    report = await service.report(DateRange.day(date(2026, 7, 31)))

    assert [p.nick_display for p in report.players] == ["Big", "Medium", "Small"]


async def test_report_leaves_discord_id_none_when_player_unbound() -> None:
    records = [_tx("ghost", DealType.PURCHASE, Decimal(100))]
    service, _tx_repo = _service(records, {})

    report = await service.report(DateRange.day(date(2026, 7, 31)))

    assert report.players[0].discord_id is None


async def test_report_empty_period_yields_empty_report() -> None:
    service, _tx_repo = _service([])

    report = await service.report(DateRange.day(date(2026, 7, 31)))

    assert report.deal_count == 0
    assert report.players == ()
    assert report.net_profit == Decimal(0)


async def test_report_queries_the_full_day_in_gmt3() -> None:
    service, transactions = _service([])

    await service.report(DateRange.day(date(2026, 7, 31)))

    start, end = transactions.list_by_period.call_args.args
    assert start.isoformat() == "2026-07-31T00:00:00+03:00"
    assert end.date() == date(2026, 7, 31)
    assert end.hour == 23 and end.minute == 59
