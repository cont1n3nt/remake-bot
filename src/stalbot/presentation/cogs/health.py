"""`/healthcheck` — database/audit/OCR-dataset status (PLAN.md §12, M11).

Hidden admin command (`@admin_only()`, same as every other command except
`/profile`/`/referrals`): database health is operational information, not
something a player needs. Uptime and the started-at timestamp are bot-level
runtime state that doesn't belong in `HealthService` (which stays
Discord-free and independently testable) — this cog merges them in.

sqlite_migration.md Э6: shows SQLite's own state instead of Sheets/cache-sync
counters — there is no sync to report on anymore.
"""

from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from stalbot.application.services.health import HealthService
from stalbot.domain.clock import format_datetime, format_duration
from stalbot.presentation.checks import admin_only
from stalbot.presentation.embeds.factory import EmbedFactory

#: PLAN.md §11.8/M9: the dataset M13 needs before OCR tuning can start.
_OCR_SAMPLE_TARGET = 150
_OCR_CONFIRMED_TARGET = 50

_SEPARATOR = "━━━━━━━━━━━━━━━━━━━━━"
_BYTES_PER_MB = 1024 * 1024


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
            f"🗄️ Схема: v{status.schema_version} • "
            f"{'✅ целостность ок' if status.integrity_ok else '🔻 ЦЕЛОСТНОСТЬ НАРУШЕНА'} • "
            f"{_size_text(status.db_size_bytes)}",
            f"👥 Игроков: {status.player_count} • 🧾 Сделок: {status.deal_count}",
            f"🕓 Последняя сделка: {_last_deal_text(status.last_deal_at)}",
            f"📬 Очередь аудита: {status.audit_queue_size}",
            f"🔍 Датасет OCR: {status.ocr_sample_count} / {_OCR_SAMPLE_TARGET} образцов • "
            f"{status.ocr_confirmed_sample_count} / {_OCR_CONFIRMED_TARGET} с эталоном",
        ]

        embed = self._embeds.info("🩺 Состояние бота", "\n".join(lines))
        await interaction.followup.send(embed=embed, ephemeral=True)


def _size_text(size_bytes: int) -> str:
    return f"{size_bytes / _BYTES_PER_MB:.1f} МБ"


def _last_deal_text(last_deal_at: datetime | None) -> str:
    if last_deal_at is None:
        return "нет данных"
    return format_datetime(last_deal_at)
