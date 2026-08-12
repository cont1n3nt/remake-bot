"""`/add` — record a deal (PLAN.md §10.1; sqlite_migration.md Э7)."""

import discord
from discord import app_commands
from discord.ext import commands

from stalbot.application.dto.transaction_request import (
    AddTransactionRequest,
    TransactionRegistrationResult,
)
from stalbot.application.services.progression import ProgressionService
from stalbot.application.services.transactions import TransactionService
from stalbot.config.settings import Settings
from stalbot.domain.clock import format_datetime
from stalbot.domain.enums import DealType
from stalbot.domain.money import evaluate_amount, format_amount
from stalbot.domain.nick import normalize_nick
from stalbot.infrastructure.cache.repositories.players import PlayersRepository
from stalbot.presentation.checks import admin_only
from stalbot.presentation.embeds.factory import EmbedFactory
from stalbot.presentation.views.confirm import ConfirmView

_DEAL_TYPE_LABEL = {
    DealType.PURCHASE: "🟢 Покупка (у меня)",
    DealType.SALE: "🟡 Продажа (мне)",
}


class TransactionsCog(commands.Cog):
    """`/add` — the shared entry point for recording a deal by hand."""

    def __init__(
        self,
        transactions: TransactionService,
        progression: ProgressionService,
        players: PlayersRepository,
        embeds: EmbedFactory,
        settings: Settings,
    ) -> None:
        """Wire the cog to the services it delegates to.

        Args:
            transactions: Writes the deal.
            progression: Reconciles roles and announces promotions.
            players: Read-only lookup for the binding-conflict warning.
            embeds: Builds every embed this cog sends.
            settings: For the reviews-channel reminder.
        """
        self._transactions = transactions
        self._progression = progression
        self._players = players
        self._embeds = embeds
        self._settings = settings

    @app_commands.command(name="add", description="🛡️ [Админ] 🧾 Зафиксировать сделку")
    @app_commands.describe(
        тип="Покупка (у меня) или Продажа (мне)",
        ник="Игровой ник",
        сумма="Сумма сделки, например 299 900 или 250к",
        реферал_ник="Игровой ник пригласившего (опционально)",
        реферал_discord="Discord-аккаунт пригласившего (опционально)",
    )
    @app_commands.choices(
        тип=[
            app_commands.Choice(
                name=_DEAL_TYPE_LABEL[DealType.PURCHASE], value=DealType.PURCHASE.value
            ),
            app_commands.Choice(name=_DEAL_TYPE_LABEL[DealType.SALE], value=DealType.SALE.value),
        ]
    )
    @app_commands.rename(discord_member="discord")
    @admin_only()
    async def add(
        self,
        interaction: discord.Interaction,
        тип: app_commands.Choice[str],
        ник: str,
        discord_member: discord.Member,
        сумма: str,
        реферал_ник: str | None = None,
        реферал_discord: discord.Member | None = None,
    ) -> None:
        """Handle `/add`: validate, write, sync progression, respond."""
        await interaction.response.defer(ephemeral=True)

        amount = evaluate_amount(сумма)
        deal_type = DealType(тип.value)

        nick_norm = normalize_nick(ник)
        referrer_norm = normalize_nick(реферал_ник) if реферал_ник else None
        if referrer_norm is not None and referrer_norm == nick_norm:
            embed = self._embeds.error("Ошибка", "Реферал не может быть тем же игроком.")
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        warnings: list[str] = []
        if реферал_ник and реферал_discord is None:
            warnings.append(
                "⚠️ Указан ник реферала без его Discord-аккаунта — привязка не выполнена."
            )

        force_rebind = False
        existing = await self._players.get_by_nick(nick_norm)
        if (
            existing is not None
            and existing.discord_id is not None
            and existing.discord_id != discord_member.id
        ):
            confirmed = await self._confirm_rebind(
                interaction, ник, existing.discord_id, discord_member
            )
            if not confirmed:
                embed = self._embeds.info("Отменено", "Сделка не была зафиксирована.")
                await interaction.followup.send(embed=embed, ephemeral=True)
                return
            force_rebind = True

        result = await self._transactions.register(
            AddTransactionRequest(
                nick=ник,
                deal_type=deal_type,
                amount=amount,
                discord_id=discord_member.id,
                idempotency_key=str(interaction.id),
                referrer_nick=реферал_ник,
                force_rebind=force_rebind,
            )
        )

        sync_nicks = [nick_norm, *([referrer_norm] if referrer_norm is not None else [])]
        channel = interaction.channel
        if isinstance(channel, discord.abc.Messageable):
            await self._progression.sync(sync_nicks, announce_to=channel)

        await self._send_confirmation(
            interaction, result, deal_type, ник, discord_member, реферал_ник, warnings
        )
        await self._send_public_notice(interaction, ник)

    async def _confirm_rebind(
        self,
        interaction: discord.Interaction,
        nick: str,
        old_discord_id: int,
        new_member: discord.Member,
    ) -> bool:
        embed = self._embeds.warning(
            "⚠️ Ник уже привязан",
            f"Игровой ник **{nick}** уже привязан к другому "
            f"Discord-аккаунту (<@{old_discord_id}>).\n"
            f"Перепривязать на {new_member.mention}?",
        )
        view = ConfirmView(author_id=interaction.user.id)
        message = await interaction.followup.send(embed=embed, view=view, ephemeral=True, wait=True)
        view.message = message
        await view.wait()
        return bool(view.confirmed)

    async def _send_confirmation(
        self,
        interaction: discord.Interaction,
        result: TransactionRegistrationResult,
        deal_type: DealType,
        nick: str,
        member: discord.Member,
        referrer_nick: str | None,
        warnings: list[str],
    ) -> None:
        lines = [
            f"📌 Тип: {_DEAL_TYPE_LABEL[deal_type]}",
            f"👤 Ник: {nick}",
            f"💬 Discord: {member.mention}",
            f"💰 Сумма: {format_amount(result.deal.amount)}",
            f"🪙 Начислено: {result.deal.coins} Coins • ⚡ {result.deal.xp} XP",
        ]
        if referrer_nick:
            lines.append(f"🤝 Реферал: {referrer_nick}")
        lines.append(f"🕒 Дата: {format_datetime(result.deal.occurred_at)}")
        if result.discord_bound:
            lines.append("🔗 Discord ID привязан к нику")
        lines.extend(warnings)

        embed = self._embeds.success("✅ Сделка зафиксирована", "\n".join(lines))
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def _send_public_notice(self, interaction: discord.Interaction, nick: str) -> None:
        channel = interaction.channel
        if not isinstance(channel, discord.abc.Messageable):
            return
        embed = self._embeds.success(
            "🧾 Сделка завершена",
            f"Сделка с **{nick}** зафиксирована.\n"
            f"Пожалуйста, оставьте отзыв в <#{self._settings.reviews_channel_id}>!",
        )
        await channel.send(embed=embed)
