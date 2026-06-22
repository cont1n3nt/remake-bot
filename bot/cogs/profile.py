import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.services.user_service import UserService
from bot.services.referral_service import ReferralService
from bot.utils.embeds import profile_embed, referral_embed, error_embed

logger = logging.getLogger("bot")


class ProfileCog(commands.Cog):

    def __init__(
        self,
        bot: commands.Bot,
        user_service: UserService,
        referral_service: ReferralService,
    ) -> None:
        self.bot = bot
        self._user_service = user_service
        self._referral_service = referral_service

    @app_commands.command(name="profile")
    @app_commands.describe(nickname="Ваш ник в таблице")
    async def profile(self, interaction: discord.Interaction, nickname: str) -> None:
        try:
            user = await asyncio.to_thread(self._user_service.get_profile, nickname)
        except Exception as e:
            logger.error("profile error: %s", e)
            await interaction.response.send_message(
                embed=error_embed("Ошибка при загрузке профиля"),
                ephemeral=True,
            )
            return

        if user is None:
            await interaction.response.send_message(
                embed=error_embed("Пользователь не найден"),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(embed=profile_embed(user))

    @app_commands.command(name="refer")
    @app_commands.describe(
        your_nick="Ваш ник в таблице",
        referrer_nick="Ник того, кто вас пригласил",
    )
    async def set_referral(
        self,
        interaction: discord.Interaction,
        your_nick: str,
        referrer_nick: str,
    ) -> None:
        try:
            await asyncio.to_thread(
                self._user_service.set_referral, your_nick, referrer_nick,
            )
        except ValueError as e:
            await interaction.response.send_message(
                embed=error_embed(str(e)),
                ephemeral=True,
            )
            return
        except Exception as e:
            logger.error("refer error: %s", e)
            await interaction.response.send_message(
                embed=error_embed("Ошибка при установке реферала"),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            embed=referral_embed(referrer_nick),
        )

    @app_commands.command(name="referrals")
    @app_commands.describe(nickname="Ваш ник в таблице")
    async def referrals(self, interaction: discord.Interaction, nickname: str) -> None:
        try:
            user = await asyncio.to_thread(self._user_service.get_referral_info, nickname)
        except Exception as e:
            logger.error("referrals error: %s", e)
            await interaction.response.send_message(
                embed=error_embed("Ошибка при загрузке рефералов"),
                ephemeral=True,
            )
            return

        if user is None:
            await interaction.response.send_message(
                embed=error_embed("Пользователь не найден"),
                ephemeral=True,
            )
            return

        embed = profile_embed(user)
        embed.add_field(name="Приглашено", value=str(user.referral_count), inline=False)

        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ProfileCog(bot, bot.user_service, bot.referral_service))
