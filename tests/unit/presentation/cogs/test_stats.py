"""Tests for `stalbot.presentation.cogs.stats.StatsCog` (PLAN.md §10.10, §10.11).

`StatsService`/`TransactionsCacheRepository` are mocked — `StatsService`'s own
aggregation is covered in `tests/unit/application/services/test_stats.py`.
This file is about whether the cog validates input, paginates, and renders
the report/log lines right.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from stalbot.application.dto.log_entry import LogEntry
from stalbot.application.dto.period_report import PeriodReport, PlayerPeriodStats
from stalbot.domain.clock import DateRange
from stalbot.domain.entities.transaction import TransactionRecord
from stalbot.domain.enums import DealType
from stalbot.domain.errors import InvalidPeriodError
from stalbot.domain.nick import NormalizedNick
from stalbot.presentation.cogs.stats import StatsCog
from stalbot.presentation.embeds.factory import EmbedFactory
from stalbot.presentation.views.logs_pager import LogsPagerView
from stalbot.presentation.views.paginated_embed import PaginatedEmbedView


def _player(**overrides: object) -> PlayerPeriodStats:
    defaults: dict[str, object] = {
        "nick_display": "Scaryyyyy",
        "discord_id": 111,
        "purchases": Decimal(5_000_000),
        "sales": Decimal(0),
    }
    defaults.update(overrides)
    return PlayerPeriodStats(**defaults)  # type: ignore[arg-type]


def _report(players: list[PlayerPeriodStats] | None = None, **overrides: object) -> PeriodReport:
    players = players if players is not None else [_player()]
    defaults: dict[str, object] = {
        "period": DateRange.day(date(2026, 7, 31)),
        "players": tuple(players),
        "deal_count": len(players),
    }
    defaults.update(overrides)
    return PeriodReport(**defaults)  # type: ignore[arg-type]


def _log_entry(**overrides: object) -> LogEntry:
    tx_defaults: dict[str, object] = {
        "row": 3,
        "at": DateRange.day(date(2026, 7, 31)).start,
        "nick": NormalizedNick("scaryyyyy"),
        "nick_display": "Scaryyyyy",
        "deal_type": DealType.PURCHASE,
        "amount": Decimal(299_900),
        "coins": 0,
        "xp": 0,
        "referrer": None,
    }
    tx_defaults["at"] = datetime(2026, 7, 31, 21, 45, tzinfo=UTC)
    defaults: dict[str, object] = {
        "day_number": 1,
        "transaction": TransactionRecord(**tx_defaults),  # type: ignore[arg-type]
        "discord_id": 111,
    }
    defaults.update(overrides)
    return LogEntry(**defaults)  # type: ignore[arg-type]


def _cog(
    *,
    report: PeriodReport | None = None,
    log_entries: list[LogEntry] | None = None,
    log_total: int | None = None,
) -> tuple[StatsCog, MagicMock, MagicMock]:
    stats = MagicMock()
    stats.report = AsyncMock(return_value=report or _report())
    transactions = MagicMock()
    entries = log_entries if log_entries is not None else [_log_entry()]
    transactions.count_all = AsyncMock(
        return_value=log_total if log_total is not None else len(entries)
    )
    transactions.list_numbered_page = AsyncMock(return_value=entries)
    clock = MagicMock()
    clock.today = MagicMock(return_value=date(2026, 7, 31))
    cog = StatsCog(stats, transactions, EmbedFactory(), clock=clock)
    return cog, stats, transactions


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
    cog, stats, _tx = _cog()
    interaction = _interaction()

    await _call_day(cog, interaction, дата="31.07.2026")

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    stats.report.assert_awaited_once_with(DateRange.day(date(2026, 7, 31)))
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "31.07.2026" in (embed.title or "")
    assert "Scaryyyyy" in (embed.description or "")
    assert "🧾 Сделок: 1" in (embed.description or "")


async def test_day_invalid_date_raises() -> None:
    cog, _stats, _tx = _cog()
    interaction = _interaction()

    with pytest.raises(InvalidPeriodError):
        await _call_day(cog, interaction, дата="not a date")


async def test_week_sends_report_for_valid_range() -> None:
    cog, stats, _tx = _cog()
    interaction = _interaction()

    await _call_week(cog, interaction, начало="25.07.2026", конец="31.07.2026")

    stats.report.assert_awaited_once_with(
        DateRange.week(date(2026, 7, 25), date(2026, 7, 31), today=date(2026, 7, 31))
    )
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "25.07.2026" in (embed.title or "")
    assert "31.07.2026" in (embed.title or "")


async def test_week_rejects_future_end_date() -> None:
    cog, _stats, _tx = _cog()
    interaction = _interaction()

    with pytest.raises(InvalidPeriodError):
        await _call_week(cog, interaction, начало="25.07.2026", конец="05.08.2026")


async def test_week_rejects_range_over_31_days() -> None:
    cog, _stats, _tx = _cog()
    interaction = _interaction()

    with pytest.raises(InvalidPeriodError):
        await _call_week(cog, interaction, начало="01.06.2026", конец="31.07.2026")


async def test_week_rejects_end_before_start() -> None:
    cog, _stats, _tx = _cog()
    interaction = _interaction()

    with pytest.raises(InvalidPeriodError):
        await _call_week(cog, interaction, начало="31.07.2026", конец="25.07.2026")


async def test_month_sends_report_with_russian_month_name() -> None:
    cog, stats, _tx = _cog()
    interaction = _interaction()

    await _call_month(cog, interaction, месяц=7, год=2026)

    stats.report.assert_awaited_once_with(DateRange.month(2026, 7))
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "Июль 2026" in (embed.title or "")


async def test_report_negative_net_profit_shows_red_marker() -> None:
    players = [_player(purchases=Decimal(1_000_000), sales=Decimal(5_000_000))]
    cog, _stats, _tx = _cog(report=_report(players, deal_count=2))
    interaction = _interaction()

    await _call_day(cog, interaction)

    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "🔻 Чистая прибыль" in (embed.description or "")


async def test_report_paginates_when_more_than_20_players() -> None:
    players = [_player(nick_display=f"Player{i}") for i in range(25)]
    cog, _stats, _tx = _cog(report=_report(players, deal_count=25))
    interaction = _interaction()

    await _call_day(cog, interaction)

    kwargs = interaction.followup.send.call_args.kwargs
    assert isinstance(kwargs["view"], PaginatedEmbedView)
    assert "стр. 1/2" in (kwargs["embed"].title or "")


async def test_logs_sends_pager_view() -> None:
    entries = [_log_entry(day_number=i, transaction=_log_entry().transaction) for i in range(1, 3)]
    cog, _stats, _tx = _cog(log_entries=entries, log_total=30)  # forces a second page
    interaction = _interaction()

    await _call_logs(cog, interaction)

    kwargs = interaction.followup.send.call_args.kwargs
    assert isinstance(kwargs["view"], LogsPagerView)
    embed = kwargs["embed"]
    assert "#1" in (embed.description or "")
    assert "🟢 Покупка" in (embed.description or "")
    assert "<@111>" in (embed.description or "")


async def test_logs_shows_placeholder_when_empty() -> None:
    cog, _stats, _tx = _cog(log_entries=[])
    interaction = _interaction()

    await _call_logs(cog, interaction)

    kwargs = interaction.followup.send.call_args.kwargs
    assert "Сделок пока нет." in (kwargs["embed"].description or "")
    assert "view" not in kwargs


async def test_logs_paginates_500_transactions_into_20_pages_within_embed_limits() -> None:
    """PLAN.md §15 M11 DoD: embed limits verified at 500 records."""
    cog, _stats, transactions = _cog(log_entries=[_log_entry()], log_total=500)
    interaction = _interaction()

    await _call_logs(cog, interaction)

    view = interaction.followup.send.call_args.kwargs["view"]
    assert isinstance(view, LogsPagerView)
    assert view.page_indicator.label == "1/20"
    assert transactions.list_numbered_page.await_count == 20
    for page in view._pages:
        assert len(page) <= 6000  # discord.Embed.__len__: total embed size cap
        assert len(page.description or "") <= 4096
