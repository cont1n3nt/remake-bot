"""`/set_referral`, `/set_rank` — manual admin overrides (PLAN.md §10.12)."""

import discord
from discord import app_commands
from discord.ext import commands

from stalbot.application.services.manual_grants import ManualGrantService
from stalbot.application.services.progression import ProgressionService
from stalbot.domain.nick import NormalizedNick, normalize_nick
from stalbot.domain.progression.ranks import RankLadder
from stalbot.presentation.checks import admin_only
from stalbot.presentation.embeds.factory import EmbedFactory
from stalbot.presentation.views.confirm import ConfirmView


class ManualCog(commands.Cog):
    """`/set_referral` and `/set_rank` — overrides that bypass the deal/formula flow."""

    def __init__(
        self,
        manual_grants: ManualGrantService,
        progression: ProgressionService,
        embeds: EmbedFactory,
        *,
        rank_ladder: RankLadder | None = None,
    ) -> None:
        """Wire the cog to the services it delegates to.

        Args:
            manual_grants: Writes the referral/rank overrides.
            progression: Reconciles roles and announces promotions afterwards
                (PLAN.md §9.2: both commands are sync triggers).
            embeds: Builds every embed this cog sends.
            rank_ladder: Defaults to a fresh `RankLadder()`.
        """
        self._manual_grants = manual_grants
        self._progression = progression
        self._embeds = embeds
        self._rank_ladder = rank_ladder or RankLadder()

    @app_commands.command(
        name="set_referral", description="🛡️ [Админ] 🤝 Указать пригласившего вручную"
    )
    @app_commands.describe(
        ник="Игровой ник игрока",
        ник_пригласившего="Игровой ник пригласившего",
        referrer_discord_member="Discord-аккаунт пригласившего",
    )
    @app_commands.rename(
        discord_member="аккаунт", referrer_discord_member="аккаунт_пригласившего"
    )
    @admin_only()
    async def set_referral(
        self,
        interaction: discord.Interaction,
        ник: str,
        discord_member: discord.Member,
        ник_пригласившего: str,
        referrer_discord_member: discord.Member,
    ) -> None:
        """Handle `/set_referral`: confirm overwrite if needed, write, sync progression."""
        await interaction.response.defer(ephemeral=True)

        nick_norm = normalize_nick(ник)
        referrer_norm = normalize_nick(ник_пригласившего)
        if referrer_norm == nick_norm:
            embed = self._embeds.error("Ошибка", "Реферал не может быть тем же игроком.")
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        existing_referrer = await self._manual_grants.current_referrer(ник)
        if existing_referrer is not None and existing_referrer != referrer_norm:
            confirmed = await self._confirm_overwrite(interaction, existing_referrer)
            if not confirmed:
                embed = self._embeds.info("Отменено", "Реферал не был изменён.")
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

        await self._manual_grants.set_referral(
            ник, ник_пригласившего, discord_member.id, referrer_discord_member.id
        )

        await self._progression.sync(
            [nick_norm, referrer_norm], announce_to=_announce_channel(interaction)
        )

        await self._send_referral_confirmation(
            interaction, ник, discord_member, ник_пригласившего, referrer_discord_member
        )
        await self._send_public_notice(
            interaction,
            "🤝 Реферал указан вручную",
            f"👤 Игрок: {ник} ({discord_member.mention})\n"
            f"🤝 Пригласил: {ник_пригласившего} ({referrer_discord_member.mention})\n"
            f"Выдано вручную администратором {interaction.user.mention}.",
        )

    async def _confirm_overwrite(
        self, interaction: discord.Interaction, existing_referrer: NormalizedNick
    ) -> bool:
        embed = self._embeds.warning(
            "⚠️ Реферал уже указан",
            f"У игрока уже указан другой реферал (**{existing_referrer}**). Перезаписать?",
        )
        view = ConfirmView(author_id=interaction.user.id)
        message = await interaction.followup.send(embed=embed, view=view, ephemeral=True, wait=True)
        view.message = message
        await view.wait()
        return bool(view.confirmed)

    async def _send_referral_confirmation(
        self,
        interaction: discord.Interaction,
        nick: str,
        member: discord.Member,
        referrer_nick: str,
        referrer_member: discord.Member,
    ) -> None:
        lines = [
            f"👤 Игрок: {nick} ({member.mention})",
            f"🤝 Пригласил: {referrer_nick} ({referrer_member.mention})",
        ]
        embed = self._embeds.success("✅ Реферал указан", "\n".join(lines))
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="set_rank", description="🛡️ [Админ] 🏅 Выдать ранг вручную (только Discord-роль)"
    )
    @app_commands.describe(ник="Игровой ник игрока", ранг="Ранг для ручной выдачи")
    @app_commands.choices(
        ранг=[app_commands.Choice(name=tier.label, value=tier.key) for tier in RankLadder().tiers]
    )
    @app_commands.rename(discord_member="аккаунт")
    @admin_only()
    async def set_rank(
        self,
        interaction: discord.Interaction,
        ник: str,
        discord_member: discord.Member,
        ранг: app_commands.Choice[str],
    ) -> None:
        """Handle `/set_rank`: grant the role, or toggle it off on a repeat call."""
        await interaction.response.defer(ephemeral=True)

        tier = self._rank_ladder.by_key(ранг.value)
        assert tier is not None  # noqa: S101 - choices are generated from this same ladder

        nick_norm = normalize_nick(ник)
        revoke = any(role.id == tier.role_id for role in discord_member.roles)

        result = await self._manual_grants.set_rank(ник, discord_member.id, tier, revoke=revoke)

        await self._progression.sync([nick_norm], announce_to=_announce_channel(interaction))

        action = "выдан" if result.granted else "снят"
        embed = self._embeds.success(
            "✅ Ранг обновлён",
            f"👤 Игрок: {ник} ({discord_member.mention})\n🏅 Ранг {tier.label} {action} вручную.",
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

        await self._send_public_notice(
            interaction,
            "🏅 Ранг изменён вручную",
            f"👤 Игрок: {ник} ({discord_member.mention})\n"
            f"🏅 Ранг {tier.label} {action} вручную.\n"
            f"Выдано вручную администратором {interaction.user.mention}.",
        )

    async def _send_public_notice(
        self, interaction: discord.Interaction, title: str, description: str
    ) -> None:
        channel = _announce_channel(interaction)
        if channel is not None:
            await channel.send(embed=self._embeds.success(title, description))


def _announce_channel(interaction: discord.Interaction) -> discord.abc.Messageable | None:
    channel = interaction.channel
    return channel if isinstance(channel, discord.abc.Messageable) else None
