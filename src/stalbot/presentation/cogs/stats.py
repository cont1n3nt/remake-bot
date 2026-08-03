"""`/logs`, `/day`, `/week`, `/month` — deal archive and period statistics.

PLAN.md §10.10, §10.11.
"""

from collections.abc import Sequence
from datetime import date
from typing import Final

import discord
from discord import app_commands
from discord.ext import commands

from stalbot.application.dto.log_entry import LogEntry
from stalbot.application.dto.period_report import PeriodReport, PlayerPeriodStats
from stalbot.application.services.stats import StatsService
from stalbot.domain.clock import DateRange, SystemClock, format_date, format_datetime, parse_date
from stalbot.domain.enums import DealType
from stalbot.domain.errors import InvalidPeriodError
from stalbot.domain.money import format_amount
from stalbot.infrastructure.cache.repositories.transactions import TransactionsCacheRepository
from stalbot.presentation.checks import admin_only
from stalbot.presentation.embeds.factory import EmbedFactory
from stalbot.presentation.views.logs_pager import LogsPagerView
from stalbot.presentation.views.paginated_embed import PaginatedEmbedView

_SEPARATOR = "━━━━━━━━━━━━━━━━━━━━━"
_PLAYERS_PAGE_SIZE: Final = 20
_LOGS_PAGE_SIZE: Final = 25
_MAX_WEEK_DAYS: Final = 31

_MONTH_NAMES: Final[dict[int, str]] = {
    1: "Январь",
    2: "Февраль",
    3: "Март",
    4: "Апрель",
    5: "Май",
    6: "Июнь",
    7: "Июль",
    8: "Август",
    9: "Сентябрь",
    10: "Октябрь",
    11: "Ноябрь",
    12: "Декабрь",
}

_DEAL_LABELS: Final[dict[DealType, str]] = {
    DealType.PURCHASE: "🟢 Покупка",
    DealType.SALE: "🟡 Продажа",
}


class StatsCog(commands.Cog):
    """`/logs`, `/day`, `/week`, `/month`."""

    def __init__(
        self,
        stats: StatsService,
        transactions: TransactionsCacheRepository,
        embeds: EmbedFactory,
        *,
        clock: SystemClock | None = None,
    ) -> None:
        """Wire the cog to the services it delegates to.

        Args:
            stats: Aggregates transactions into a `PeriodReport`.
            transactions: Read directly (not through `stats`) for `/logs`'s
                plain numbered-page listing.
            embeds: Builds every embed this cog sends.
            clock: Time source for `/week`'s "not in the future" check.
                Defaults to a real `SystemClock`.
        """
        self._stats = stats
        self._transactions = transactions
        self._embeds = embeds
        self._clock = clock or SystemClock()

    @app_commands.command(name="logs", description="🛡️ [Админ] 🧾 Архив сделок")
    @admin_only()
    async def logs(self, interaction: discord.Interaction) -> None:
        """Handle `/logs`: browse every cached deal, newest first, 25/page."""
        await interaction.response.defer(ephemeral=True)
        pages = await self._build_log_pages()
        if len(pages) == 1:
            await interaction.followup.send(embed=pages[0], ephemeral=True)
            return
        view = LogsPagerView(pages=pages, author_id=interaction.user.id)
        message = await interaction.followup.send(
            embed=view.current, view=view, ephemeral=True, wait=True
        )
        view.message = message

    @app_commands.command(name="day", description="🛡️ [Админ] 📊 Статистика за день")
    @app_commands.describe(дата="Дата в формате ДД.ММ.ГГГГ")
    @admin_only()
    async def day(self, interaction: discord.Interaction, дата: str) -> None:
        """Handle `/day`: aggregate every deal recorded on one calendar day."""
        await interaction.response.defer(ephemeral=True)
        target_day = parse_date(дата)
        report = await self._stats.report(DateRange.day(target_day))
        await self._send_report(
            interaction, title=f"📊 Статистика за {format_date(target_day)}", report=report
        )

    @app_commands.command(name="week", description="🛡️ [Админ] 📊 Статистика за период")
    @app_commands.describe(начало="Начало периода, ДД.ММ.ГГГГ", конец="Конец периода, ДД.ММ.ГГГГ")
    @admin_only()
    async def week(self, interaction: discord.Interaction, начало: str, конец: str) -> None:
        """Handle `/week`: aggregate every deal within an explicit date range."""
        await interaction.response.defer(ephemeral=True)
        start, end = parse_date(начало), parse_date(конец)
        _validate_week_range(start, end, today=self._clock.today())
        report = await self._stats.report(DateRange.week(start, end))
        title = f"📊 Статистика с {format_date(start)} по {format_date(end)}"
        await self._send_report(interaction, title=title, report=report)

    @app_commands.command(name="month", description="🛡️ [Админ] 📊 Статистика за месяц")
    @app_commands.describe(месяц="Месяц", год="Год")
    @app_commands.choices(
        месяц=[app_commands.Choice(name=name, value=i) for i, name in _MONTH_NAMES.items()]
    )
    @admin_only()
    async def month(
        self, interaction: discord.Interaction, месяц: app_commands.Choice[int], год: int
    ) -> None:
        """Handle `/month`: aggregate every deal within a calendar month."""
        await interaction.response.defer(ephemeral=True)
        report = await self._stats.report(DateRange.month(год, месяц.value))
        title = f"📊 Статистика за {_MONTH_NAMES[месяц.value]} {год}"
        await self._send_report(interaction, title=title, report=report)

    async def _send_report(
        self, interaction: discord.Interaction, *, title: str, report: PeriodReport
    ) -> None:
        pages = _render_period_pages(self._embeds, title, report)
        if len(pages) == 1:
            await interaction.followup.send(embed=pages[0], ephemeral=True)
            return
        pager = PaginatedEmbedView(pages=pages, author_id=interaction.user.id)
        message = await interaction.followup.send(
            embed=pager.current, view=pager, ephemeral=True, wait=True
        )
        pager.message = message

    async def _build_log_pages(self) -> list[discord.Embed]:
        total = await self._transactions.count_all()
        if total == 0:
            return [self._embeds.info("🧾 Архив сделок", "Сделок пока нет.")]

        page_count = -(-total // _LOGS_PAGE_SIZE)  # ceil division
        pages: list[discord.Embed] = []
        for page_index in range(page_count):
            entries = await self._transactions.list_numbered_page(
                offset=page_index * _LOGS_PAGE_SIZE, limit=_LOGS_PAGE_SIZE
            )
            lines = [_format_log_line(entry) for entry in entries]
            title = f"🧾 Архив сделок (стр. {page_index + 1}/{page_count})"
            pages.append(self._embeds.info(title, "\n".join(lines)))
        return pages


def _validate_week_range(start: date, end: date, *, today: date) -> None:
    """Enforce `/week`'s range rules (PLAN.md §10.11): `end >= start` is `DateRange`'s job."""
    if end > today:
        raise InvalidPeriodError("конец периода не может быть в будущем")
    if (end - start).days + 1 > _MAX_WEEK_DAYS:
        raise InvalidPeriodError(f"диапазон не может превышать {_MAX_WEEK_DAYS} дней")


def _format_log_line(entry: LogEntry) -> str:
    tx = entry.transaction
    tag = f"<@{entry.discord_id}>" if entry.discord_id is not None else "—"
    return (
        f"#{entry.day_number} │ {format_datetime(tx.at)} │ {tx.nick_display} │ "
        f"{tag} │ {_DEAL_LABELS[tx.deal_type]} │ {format_amount(tx.amount)}"
    )


def _render_period_pages(
    embeds: EmbedFactory, title: str, report: PeriodReport
) -> list[discord.Embed]:
    chunks = _chunk(report.players, _PLAYERS_PAGE_SIZE) or [()]
    footer = _footer_lines(report)
    pages: list[discord.Embed] = []
    for index, chunk in enumerate(chunks, start=1):
        table_lines = [_format_player_line(p) for p in chunk] or ["Сделок не было."]
        body = ["```", *table_lines, "```", _SEPARATOR, *footer]
        page_title = title if len(chunks) == 1 else f"{title} (стр. {index}/{len(chunks)})"
        pages.append(embeds.info(page_title, "\n".join(body)))
    return pages


def _format_player_line(player: PlayerPeriodStats) -> str:
    tag = f"<@{player.discord_id}>" if player.discord_id is not None else "—"
    purchases = format_amount(player.purchases, currency=False) if player.purchases else "—"
    sales = format_amount(player.sales, currency=False) if player.sales else "—"
    turnover = format_amount(player.turnover, currency=False)
    return f"{player.nick_display} │ {tag} │ {purchases} │ {sales} │ {turnover}"


def _footer_lines(report: PeriodReport) -> list[str]:
    net = report.net_profit
    net_marker = "🔻" if net < 0 else "💰"
    return [
        f"🟢 Всего покупок (у меня): {format_amount(report.total_purchases)}",
        f"🟡 Всего продаж (мне): {format_amount(report.total_sales)}",
        f"{net_marker} Чистая прибыль: {format_amount(net)}",
        f"🧾 Сделок: {report.deal_count}",
    ]


def _chunk[T](items: Sequence[T], size: int) -> list[Sequence[T]]:
    return [items[i : i + size] for i in range(0, len(items), size)]
