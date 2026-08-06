"""`/tag` — DM a player a reminder about the ticket they're in (PLAN.md §10.13).

Pure presentation: there is no domain rule here beyond "send this embed, or
fall back if DMs are closed", so unlike the other admin commands this cog
does not delegate to an `application/services` use-case.
"""

import discord
from discord import app_commands
from discord.ext import commands

from stalbot.presentation.checks import admin_only
from stalbot.presentation.embeds.factory import EmbedFactory


class TagCog(commands.Cog):
    """`/tag` — notifies a member about the channel the command was run in."""

    def __init__(self, embeds: EmbedFactory) -> None:
        """Wire the cog to the embed factory.

        Args:
            embeds: Builds every embed this cog sends.
        """
        self._embeds = embeds

    @app_commands.command(name="tag", description="🛡️ [Админ] 🔔 Уведомить пользователя о тикете")
    @app_commands.describe(пользователь="Кого уведомить")
    @admin_only()
    async def tag(self, interaction: discord.Interaction, пользователь: discord.Member) -> None:
        """Handle `/tag`: DM the member a link back to this channel, with a fallback."""
        await interaction.response.defer(ephemeral=True)

        channel_name = getattr(interaction.channel, "name", None) or "тикет"
        guild_name = getattr(interaction.guild, "name", None) or "сервера"
        embed = self._embeds.info(
            "🔔 Уведомление по тикету",
            f"Привет! Загляните, пожалуйста, в тикет **#{channel_name}** на сервере "
            f"**{guild_name}** и ответьте, когда будет время.",
        )
        view = _ticket_link_view(interaction.guild_id, interaction.channel_id)

        try:
            if view is not None:
                await пользователь.send(embed=embed, view=view)
            else:
                await пользователь.send(embed=embed)
        except discord.Forbidden:
            await self._notify_dm_closed(interaction, пользователь)
            return

        confirmation = self._embeds.success(
            "✅ Уведомление отправлено", f"{пользователь.mention} получил(а) DM."
        )
        await interaction.followup.send(embed=confirmation, ephemeral=True)

    async def _notify_dm_closed(
        self, interaction: discord.Interaction, member: discord.Member
    ) -> None:
        channel = interaction.channel
        if isinstance(channel, discord.abc.Messageable):
            await channel.send(
                content=member.mention,
                embed=self._embeds.warning(
                    "🔔 Напоминание",
                    "Пожалуйста, проверьте этот тикет и ответьте, когда будет время.",
                ),
            )
        embed = self._embeds.warning(
            "⚠️ DM закрыты",
            f"Не удалось отправить личное сообщение {member.mention} — DM закрыты.",
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


def _ticket_link_view(guild_id: int | None, channel_id: int | None) -> discord.ui.View | None:
    """Build the grey `🎫 Перейти к тикету` link button, if the channel is addressable.

    Link buttons are the one style Discord always renders grey — this is
    why no explicit color choice is needed (PLAN.md §10.13).
    """
    if guild_id is None or channel_id is None:
        return None
    view = discord.ui.View()
    view.add_item(
        discord.ui.Button(
            label="🎫 Перейти к тикету",
            style=discord.ButtonStyle.link,
            url=f"https://discord.com/channels/{guild_id}/{channel_id}",
        )
    )
    return view
