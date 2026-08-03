"""`/healthcheck` — Sheets/cache/audit/OCR-dataset status (PLAN.md §12, M11).

Hidden admin command (`@admin_only()`, same as every other command except
`/profile`/`/referrals`): Sheets/cache health is operational information,
not something a player needs. Uptime and the started-at timestamp are
bot-level runtime state that doesn't belong in `HealthService` (which stays
Discord-free and independently testable) — this cog merges them in.
"""

from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from stalbot.application.dto.health_status import HealthStatus
from stalbot.application.services.health import HealthService
from stalbot.domain.clock import format_duration
from stalbot.presentation.checks import admin_only
from stalbot.presentation.embeds.factory import EmbedFactory

#: PLAN.md §11.8/M9: the dataset M13 needs before OCR tuning can start.
_OCR_SAMPLE_TARGET = 150
_OCR_CONFIRMED_TARGET = 50

_SEPARATOR = "━━━━━━━━━━━━━━━━━━━━━"


class HealthCog(commands.Cog):
    """`/healthcheck` — a point-in-time operational snapshot."""

    def __init__(
        self,
        health: HealthService,
        embeds: EmbedFactory,
        *,
        started_at: datetime,
    ) -> None:
        """Wire the cog to the service it delegates to.

        Args:
            health: Aggregates Sheets/cache/audit/OCR-dataset counters.
            embeds: Builds every embed this cog sends.
            started_at: When the bot finished its startup sync — the
                uptime clock's zero point.
        """
        self._health = health
        self._embeds = embeds
        self._started_at = started_at

    @app_commands.command(name="healthcheck", description="🛡️ [Админ] 🩺 Состояние бота")
    @admin_only()
    async def healthcheck(self, interaction: discord.Interaction) -> None:
        """Handle `/healthcheck`: build and show the current health snapshot."""
        await interaction.response.defer(ephemeral=True)
        status = await self._health.snapshot()
        uptime = (discord.utils.utcnow() - self._started_at).total_seconds()

        lines = [
            _SEPARATOR,
            f"⏱️ Uptime: {format_duration(uptime)}",
            f"📡 Sheets: {status.sheets_read_requests} чтений • "
            f"{status.sheets_write_requests} записей",
            f"💾 Кэш: {_cache_age_text(status.cache_age_seconds)} • "
            f"{_hit_rate_text(status.cache_hit_rate)}",
            _users_sync_line(status),
            _items_sync_line(status),
            f"📬 Очередь аудита: {status.audit_queue_size}",
            f"🔍 Датасет OCR: {status.ocr_sample_count} / {_OCR_SAMPLE_TARGET} образцов • "
            f"{status.ocr_confirmed_sample_count} / {_OCR_CONFIRMED_TARGET} с эталоном",
        ]

        embed = self._embeds.info("🩺 Состояние бота", "\n".join(lines))
        await interaction.followup.send(embed=embed, ephemeral=True)


def _cache_age_text(age_seconds: float | None) -> str:
    if age_seconds is None:
        return "нет данных"
    return f"обновлён {format_duration(age_seconds)} назад"


def _hit_rate_text(hit_rate: float | None) -> str:
    return f"hit-rate {hit_rate:.0%}" if hit_rate is not None else "hit-rate н/д"


def _users_sync_line(status: HealthStatus) -> str:
    report = status.last_users_sync
    if report is None:
        return "🔄 Синк пользователей: ещё не выполнялся"
    return (
        f"🔄 Синк пользователей: {report.users_synced} польз. • "
        f"{report.transactions_synced} сделок ({report.transactions_skipped} пропущено) • "
        f"{report.duration_seconds:.2f} с • формул свободно: {report.formula_free_rows}"
    )


def _items_sync_line(status: HealthStatus) -> str:
    report = status.last_items_sync
    if report is None:
        return "🔄 Синк предметов: ещё не выполнялся"
    return f"🔄 Синк предметов: {report.items_synced} предметов • {report.duration_seconds:.2f} с"
