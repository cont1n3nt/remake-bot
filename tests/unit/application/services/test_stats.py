"""Tests for `stalbot.application.services.stats.StatsService` (PLAN.md §10.11)."""

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock

from stalbot.application.services.stats import StatsService
from stalbot.domain.clock import DateRange
from stalbot.domain.entities.deal import Deal
from stalbot.domain.entities.player import Player
from stalbot.domain.enums import DealSource, DealType, OccurredAtKind
from stalbot.domain.money import Rub
from stalbot.domain.nick import NormalizedNick

_NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


def _deal(player_id: int, deal_type: DealType, amount: int, **overrides: object) -> Deal:
    defaults: dict[str, object] = {
        "id": 1,
        "player_id": player_id,
        "occurred_at": _NOW,
        "occurred_at_kind": OccurredAtKind.BOT,
        "deal_type": deal_type,
        "amount": Rub(amount),
        "coins": 0,
        "xp": 0,
        "rank_at_deal": None,
        "booster_at_deal": False,
        "recorded_by": None,
        "source": DealSource.ADD,
        "legacy_sheet_row": None,
        "created_at": _NOW,
    }
    defaults.update(overrides)
    return Deal(**defaults)  # type: ignore[arg-type]


def _player(player_id: int, nick: str, discord_id: int | None) -> Player:
    return Player(
        id=player_id,
        nick_norm=NormalizedNick(nick),
        nick_display=nick.capitalize(),
        discord_id=discord_id,
        referrer_player_id=None,
        is_booster=False,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _service(
    deals: list[Deal], players: dict[int, Player] | None = None
) -> tuple[StatsService, MagicMock]:
    deals_repo = MagicMock()
    deals_repo.list_by_period = AsyncMock(return_value=deals)
    players_repo = MagicMock()
    players_by_id = players or {}
    players_repo.get_by_ids = AsyncMock(
        side_effect=lambda ids: {i: players_by_id[i] for i in ids if i in players_by_id}
    )
    return StatsService(deals_repo, players_repo), deals_repo


async def test_report_aggregates_purchases_and_sales_per_player() -> None:
    deals = [
        _deal(1, DealType.PURCHASE, 5_000_000),
        _deal(1, DealType.PURCHASE, 1_000_000),
        _deal(1, DealType.SALE, 500_000),
    ]
    service, _deals_repo = _service(deals, {1: _player(1, "scaryyyyy", 111)})

    report = await service.report(DateRange.day(date(2026, 7, 31)))

    assert report.deal_count == 3
    assert len(report.players) == 1
    player = report.players[0]
    assert player.discord_id == 111
    assert player.purchases == 6_000_000
    assert player.sales == 500_000
    assert player.turnover == 6_500_000


async def test_report_computes_totals_and_net_profit() -> None:
    deals = [
        _deal(1, DealType.PURCHASE, 28_500_000),
        _deal(2, DealType.SALE, 12_300_000),
    ]
    service, _deals_repo = _service(deals, {1: _player(1, "alice", 1), 2: _player(2, "bob", 2)})

    report = await service.report(DateRange.day(date(2026, 7, 31)))

    assert report.total_purchases == 28_500_000
    assert report.total_sales == 12_300_000
    assert report.net_profit == 16_200_000


async def test_report_net_profit_can_be_negative() -> None:
    deals = [
        _deal(1, DealType.PURCHASE, 1_000_000),
        _deal(1, DealType.SALE, 5_000_000),
    ]
    service, _deals_repo = _service(deals, {1: _player(1, "alice", 1)})

    report = await service.report(DateRange.day(date(2026, 7, 31)))

    assert report.net_profit == -4_000_000


async def test_report_sorts_players_by_turnover_descending() -> None:
    deals = [
        _deal(1, DealType.PURCHASE, 100),
        _deal(2, DealType.PURCHASE, 10_000),
        _deal(3, DealType.PURCHASE, 1_000),
    ]
    service, _deals_repo = _service(
        deals,
        {
            1: _player(1, "small", None),
            2: _player(2, "big", None),
            3: _player(3, "medium", None),
        },
    )

    report = await service.report(DateRange.day(date(2026, 7, 31)))

    assert [p.nick_display for p in report.players] == ["Big", "Medium", "Small"]


async def test_report_leaves_discord_id_none_when_player_unbound() -> None:
    deals = [_deal(1, DealType.PURCHASE, 100)]
    service, _deals_repo = _service(deals, {1: _player(1, "ghost", None)})

    report = await service.report(DateRange.day(date(2026, 7, 31)))

    assert report.players[0].discord_id is None


async def test_report_empty_period_yields_empty_report() -> None:
    service, _deals_repo = _service([])

    report = await service.report(DateRange.day(date(2026, 7, 31)))

    assert report.deal_count == 0
    assert report.players == ()
    assert report.net_profit == 0


async def test_report_queries_the_full_day_in_gmt3() -> None:
    service, deals_repo = _service([])

    await service.report(DateRange.day(date(2026, 7, 31)))

    start, end = deals_repo.list_by_period.call_args.args
    assert start.isoformat() == "2026-07-31T00:00:00+03:00"
    assert end.date() == date(2026, 7, 31)
    assert end.hour == 23 and end.minute == 59


async def test_report_marks_interpolated_deals_for_display() -> None:
    deals = [_deal(1, DealType.PURCHASE, 100, occurred_at_kind=OccurredAtKind.SHEET_INTERPOLATED)]
    service, _deals_repo = _service(deals, {1: _player(1, "alice", 1)})

    report = await service.report(DateRange.day(date(2026, 7, 31)))

    assert report.deals[0].deal.occurred_at_kind is OccurredAtKind.SHEET_INTERPOLATED
