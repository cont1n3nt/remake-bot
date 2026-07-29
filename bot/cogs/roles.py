"""Ког для управления Discord-ролями: автовыдача по сделкам, ручные команды."""

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot.services.role_service import (
    RoleService,
    RANK_ROLES,
    REFERRAL_ROLES,
)

logger = logging.getLogger("bot")


async def _send_role_notification(
    channel: discord.TextChannel,
    member: discord.Member,
    nickname: str,
    role_name: str,
    role_type: str,
    role_map: dict[str, int],
) -> None:
    mention = member.mention
    role_id = role_map.get(role_name, 0)
    role_mention = f"<@&{role_id}>" if role_id else role_name
    msg = (
        f"🎉 **Выдача {role_type} роли!**\n\n"
        f"1️⃣ Пользователь: {mention}\n"
        f"2️⃣ Игровой никнейм: `{nickname}`\n"
        f"3️⃣ Полученная роль: {role_mention}\n\n"
        f"Поздравляем с получением новой роли! 🌟"
    )
    try:
        await channel.send(msg)
    except Exception as e:
        logger.warning("role notification failed: %s", e)


class RolesCog(commands.Cog):
    """Автоматическое и ручное назначение ранговых / реферальных ролей."""

    def __init__(self, bot: commands.Bot, role_service: RoleService) -> None:
        self.bot = bot
        self.role_service = role_service
        self.role_service.set_bot(bot)

    @app_commands.command(name="set_rank", description="🏅 (Админ) Ручное назначение игрового ранга/роли пользователю сервера")
    @app_commands.describe(user="Участник сервера", rank="Название ранга", nickname="Игровой никнейм (для уведомления)")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.choices(rank=[
        app_commands.Choice(name="Standard", value="Standard"),
        app_commands.Choice(name="Premium", value="Premium"),
        app_commands.Choice(name="Prestige", value="Prestige"),
        app_commands.Choice(name="Elite", value="Elite"),
        app_commands.Choice(name="Legend", value="Legend"),
    ])
    async def set_rank(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        rank: str,
        nickname: str = "",
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        result = await self.role_service.set_specific_role(
            member=user,
            role_name=rank,
            role_map=RANK_ROLES,
            group_label="Rank",
        )

        if result is None:
            current = self.role_service._get_current_from_map(user, RANK_ROLES)
            if current and current.id == RANK_ROLES.get(rank):
                text = f"Роль `{rank}` уже назначена пользователю {user.mention}."
            else:
                text = "Не удалось назначить роль. Проверьте права бота."
        else:
            text = f"Роль `{result}` назначена пользователю {user.mention}."
            if interaction.guild and nickname:
                await _send_role_notification(
                    interaction.channel, user, nickname, rank, "ранговая", RANK_ROLES,
                )

        await interaction.followup.send(text, ephemeral=True)

    @set_rank.error
    async def set_rank_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError,
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            text = "Недостаточно прав. Требуются права администратора."
        else:
            text = f"Ошибка: {error}"
        try:
            await interaction.response.send_message(text, ephemeral=True)
        except discord.errors.InteractionResponded:
            await interaction.followup.send(text, ephemeral=True)

    @app_commands.command(name="set_referral", description="🔗 (Админ) Ручная привязка реферальной роли пользователю на сервере")
    @app_commands.describe(user="Участник сервера", role="Название реферальной роли", nickname="Игровой никнейм (для уведомления)")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.choices(role=[
        app_commands.Choice(name="Скаут", value="Скаут"),
        app_commands.Choice(name="Промоутер", value="Промоутер"),
        app_commands.Choice(name="Вербовщик", value="Вербовщик"),
        app_commands.Choice(name="Амбассадор", value="Амбассадор"),
        app_commands.Choice(name="Рекламный Барон", value="Рекламный Барон"),
    ])
    async def set_referral(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        role: str,
        nickname: str = "",
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        result = await self.role_service.set_specific_role(
            member=user,
            role_name=role,
            role_map=REFERRAL_ROLES,
            group_label="Referral",
        )

        if result is None:
            current = self.role_service._get_current_from_map(user, REFERRAL_ROLES)
            if current and current.id == REFERRAL_ROLES.get(role):
                text = f"Роль `{role}` уже назначена пользователю {user.mention}."
            else:
                text = "Не удалось назначить роль. Проверьте права бота."
        else:
            text = f"Роль `{result}` назначена пользователю {user.mention}."
            if interaction.guild and nickname:
                await _send_role_notification(
                    interaction.channel, user, nickname, role, "реферальная", REFERRAL_ROLES,
                )

        await interaction.followup.send(text, ephemeral=True)

    @set_referral.error
    async def set_referral_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError,
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            text = "Недостаточно прав. Требуются права администратора."
        else:
            text = f"Ошибка: {error}"
        try:
            await interaction.response.send_message(text, ephemeral=True)
        except discord.errors.InteractionResponded:
            await interaction.followup.send(text, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    role_service = RoleService()
    await bot.add_cog(RolesCog(bot, role_service))
