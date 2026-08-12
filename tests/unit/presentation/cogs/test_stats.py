"""Tests for `stalbot.presentation.cogs.stats.StatsCog` (PLAN.md §10.10, §10.11).

`StatsService`/`DealsRepository`/`PlayersRepository` are mocked —
`StatsService`'s own aggregation is covered in
`tests/unit/application/services/test_stats.py`. This file is about whether
the cog validates input, paginates, and renders the report/log lines right.
"""

from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from stalbot.application.dto.period_report import PeriodDeal, PeriodReport, PlayerPeriodStats
from stalbot.domain.clock import DateRange
from stalbot.domain.entities.deal import Deal
from stalbot.domain.entities.player import Player
from stalbot.domain.enums import DealSource, DealType, OccurredAtKind
from stalbot.domain.errors import InvalidPeriodError
from stalbot.domain.money import Rub
from stalbot.domain.nick import NormalizedNick
from stalbot.presentation.cogs.stats import StatsCog
from stalbot.presentation.embeds.factory import EmbedFactory
from stalbot.presentation.views.logs_pager import LogsPagerView
from stalbot.presentation.views.paginated_embed import PaginatedEmbedView

_NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


def _player_stats(**overrides: object) -> PlayerPeriodStats:
    defaults: dict[str, object] = {
        "nick_display": "Scaryyyyy",
        "discord_id": 111,
        "purchases": Rub(5_000_000),
        "sales": Rub(0),
    }
    defaults.update(overrides)
    return PlayerPeriodStats(**defaults)  # type: ignore[arg-type]


def _deal(**overrides: object) -> Deal:
    defaults: dict[str, object] = {
        "id": 1,
        "player_id": 1,
        "occurred_at": datetime(2026, 7, 31, 21, 45, tzinfo=UTC),
        "occurred_at_kind": OccurredAtKind.BOT,
        "deal_type": DealType.PURCHASE,
        "amount": Rub(299_900),
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


def _player(player_id: int, nick_display: str, discord_id: int | None) -> Player:
    return Player(
        id=player_id,
        nick_norm=NormalizedNick(nick_display.lower()),
        nick_display=nick_display,
        discord_id=discord_id,
        referrer_player_id=None,
        is_booster=False,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _report(players: list[PlayerPeriodStats] | None = None, **overrides: object) -> PeriodReport:
    players = players if players is not None else [_player_stats()]
    defaults: dict[str, object] = {
        "period": DateRange.day(date(2026, 7, 31)),
        "players": tuple(players),
        "deal_count": len(players),
    }
    defaults.update(overrides)
    return PeriodReport(**defaults)  # type: ignore[arg-type]


def _cog(
    *,
    report: PeriodReport | None = None,
    numbered_deals: list[tuple[int, Deal]] | None = None,
    log_total: int | None = None,
    players_by_id: dict[int, Player] | None = None,
) -> tuple[StatsCog, MagicMock, MagicMock, MagicMock]:
    stats = MagicMock()
    stats.report = AsyncMock(return_value=report or _report())
    deals_repo = MagicMock()
    entries = numbered_deals if numbered_deals is not None else [(1, _deal())]
    deals_repo.count = AsyncMock(return_value=log_total if log_total is not None else len(entries))
    deals_repo.list_numbered_page = AsyncMock(return_value=entries)
    players_repo = MagicMock()
    by_id = players_by_id if players_by_id is not None else {1: _player(1, "Scaryyyyy", 111)}
    players_repo.get_by_ids = AsyncMock(
        side_effect=lambda ids: {i: by_id[i] for i in ids if i in by_id}
    )
    clock = MagicMock()
    clock.today = MagicMock(return_value=date(2026, 7, 31))
    cog = StatsCog(stats, deals_repo, players_repo, EmbedFactory(), clock=clock)
    return cog, stats, deals_repo, players_repo


def _interaction() -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock(id=1)
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock(return_value=MagicMock(spec=discord.Message))
    return interaction


async def _call_day(cog: StatsCog, interaction: MagicMock, *, дата: str = "31.07.2026") -> None:
    callback: Any = StatsCog.day.callback
    await callback(cog, interaction, дата)


async def _call_week(
    cog: StatsCog, interaction: MagicMock, *, начало: str = "25.07.2026", конец: str = "31.07.2026"
) -> None:
    callback: Any = StatsCog.week.callback
    await callback(cog, interaction, начало, конец)


async def _call_month(
    cog: StatsCog, interaction: MagicMock, *, месяц: int = 7, год: int = 2026
) -> None:
    callback: Any = StatsCog.month.callback
    choice = discord.app_commands.Choice(name="Июль", value=месяц)
    await callback(cog, interaction, choice, год)


async def _call_logs(cog: StatsCog, interaction: MagicMock) -> None:
    callback: Any = StatsCog.logs.callback
    await callback(cog, interaction)


async def test_day_sends_report_embed() -> None:
    cog, stats, _deals, _players = _cog()
    interaction = _interaction()

    await _call_day(cog, interaction, дата="31.07.2026")

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    stats.report.assert_awaited_once_with(DateRange.day(date(2026, 7, 31)))
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "31.07.2026" in (embed.title or "")
    assert "Scaryyyyy" in (embed.description or "")
    assert "🧾 Сделок: 1" in (embed.description or "")


async def test_day_invalid_date_raises() -> None:
    cog, _stats, _deals, _players = _cog()
    interaction = _interaction()

    with pytest.raises(InvalidPeriodError):
        await _call_day(cog, interaction, дата="not a date")


async def test_week_sends_report_for_valid_range() -> None:
    cog, stats, _deals, _players = _cog()
    interaction = _interaction()

    await _call_week(cog, interaction, начало="25.07.2026", конец="31.07.2026")

    stats.report.assert_awaited_once_with(
        DateRange.week(date(2026, 7, 25), date(2026, 7, 31), today=date(2026, 7, 31))
    )
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "25.07.2026" in (embed.title or "")
    assert "31.07.2026" in (embed.title or "")


async def test_week_rejects_future_end_date() -> None:
    cog, _stats, _deals, _players = _cog()
    interaction = _interaction()

    with pytest.raises(InvalidPeriodError):
        await _call_week(cog, interaction, начало="25.07.2026", конец="05.08.2026")


async def test_week_rejects_range_over_31_days() -> None:
    cog, _stats, _deals, _players = _cog()
    interaction = _interaction()

    with pytest.raises(InvalidPeriodError):
        await _call_week(cog, interaction, начало="01.06.2026", конец="31.07.2026")


async def test_week_rejects_end_before_start() -> None:
    cog, _stats, _deals, _players = _cog()
    interaction = _interaction()

    with pytest.raises(InvalidPeriodError):
        await _call_week(cog, interaction, начало="31.07.2026", конец="25.07.2026")


async def test_month_sends_report_with_russian_month_name() -> None:
    cog, stats, _deals, _players = _cog()
    interaction = _interaction()

    await _call_month(cog, interaction, месяц=7, год=2026)

    stats.report.assert_awaited_once_with(DateRange.month(2026, 7))
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "Июль 2026" in (embed.title or "")


async def test_report_negative_net_profit_shows_red_marker() -> None:
    players = [_player_stats(purchases=Rub(1_000_000), sales=Rub(5_000_000))]
    cog, _stats, _deals, _players_repo = _cog(report=_report(players, deal_count=2))
    interaction = _interaction()

    await _call_day(cog, interaction)

    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "🔻 Чистая прибыль" in (embed.description or "")


async def test_report_paginates_when_more_than_20_players() -> None:
    players = [_player_stats(nick_display=f"Player{i}") for i in range(25)]
    cog, _stats, _deals, _players = _cog(report=_report(players, deal_count=25))
    interaction = _interaction()

    await _call_day(cog, interaction)

    kwargs = interaction.followup.send.call_args.kwargs
    assert isinstance(kwargs["view"], PaginatedEmbedView)
    assert "стр. 1/2" in (kwargs["embed"].title or "")


async def test_day_report_lists_individual_deals_with_dates() -> None:
    deals = (
        PeriodDeal(
            nick_display="Scaryyyyy",
            deal=_deal(amount=Rub(299_900), deal_type=DealType.PURCHASE),
        ),
        PeriodDeal(
            nick_display="OtherNick",
            deal=_deal(amount=Rub(150_000), deal_type=DealType.SALE),
        ),
    )
    cog, _stats, _deals_repo, _players = _cog(report=_report(deal_count=2, deals=deals))
    interaction = _interaction()

    await _call_day(cog, interaction)

    kwargs = interaction.followup.send.call_args.kwargs
    assert isinstance(kwargs["view"], PaginatedEmbedView)
    deal_pages = [
        page for page in kwargs["view"]._pages if (page.title or "").startswith("🧾 Сделки")
    ]
    assert len(deal_pages) == 1
    body = deal_pages[0].description or ""
    assert "01.08.2026 00:45" in body  # 21:45 UTC == 00:45 GMT+3 the next day
    assert "Scaryyyyy" in body
    assert "OtherNick" in body
    assert "299900" in body.replace(" ", "")


async def test_day_report_marks_interpolated_deal_as_approximate() -> None:
    deals = (
        PeriodDeal(
            nick_display="Scaryyyyy",
            deal=_deal(occurred_at_kind=OccurredAtKind.SHEET_INTERPOLATED),
        ),
    )
    cog, _stats, _deals_repo, _players = _cog(report=_report(deal_count=1, deals=deals))
    interaction = _interaction()

    await _call_day(cog, interaction)

    kwargs = interaction.followup.send.call_args.kwargs
    deal_pages = [
        page for page in kwargs["view"]._pages if (page.title or "").startswith("🧾 Сделки")
    ]
    assert "≈" in (deal_pages[0].description or "")


async def test_day_report_has_no_deal_page_when_period_had_no_deals() -> None:
    cog, _stats, _deals, _players = _cog(report=_report(deal_count=1))
    interaction = _interaction()

    await _call_day(cog, interaction)

    kwargs = interaction.followup.send.call_args.kwargs
    assert "view" not in kwargs
    assert "🧾 Сделки" not in (kwargs["embed"].title or "")


async def test_logs_sends_pager_view() -> None:
    entries = [(i, _deal()) for i in range(1, 3)]
    cog, _stats, _deals, _players = _cog(numbered_deals=entries, log_total=30)  # forces a 2nd page
    interaction = _interaction()

    await _call_logs(cog, interaction)

    kwargs = interaction.followup.send.call_args.kwargs
    assert isinstance(kwargs["view"], LogsPagerView)
    embed = kwargs["embed"]
    assert "#1" in (embed.description or "")
    assert "🟢 Покупка" in (embed.description or "")
    assert "<@111>" in (embed.description or "")


async def test_logs_shows_placeholder_when_empty() -> None:
    cog, _stats, _deals, _players = _cog(numbered_deals=[], log_total=0)
    interaction = _interaction()

    await _call_logs(cog, interaction)

    kwargs = interaction.followup.send.call_args.kwargs
    assert "Сделок пока нет." in (kwargs["embed"].description or "")
    assert "view" not in kwargs


async def test_logs_paginates_500_transactions_into_20_pages_within_embed_limits() -> None:
    """PLAN.md §15 M11 DoD: embed limits verified at 500 records."""
    cog, _stats, deals_repo, _players = _cog(numbered_deals=[(1, _deal())], log_total=500)
    interaction = _interaction()

    await _call_logs(cog, interaction)

    view = interaction.followup.send.call_args.kwargs["view"]
    assert isinstance(view, LogsPagerView)
    assert view.page_indicator.label == "1/20"
    assert deals_repo.list_numbered_page.await_count == 20
    for page in view._pages:
        assert len(page) <= 6000  # discord.Embed.__len__: total embed size cap
        assert len(page.description or "") <= 4096
