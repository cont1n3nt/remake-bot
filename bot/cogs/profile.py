import discord
from discord import app_commands
from discord.ext import commands

from bot.services.user_service import UserService


class ProfileCog(commands.Cog):

    def __init__(self, bot: commands.Bot, user_service: UserService) -> None:
        self.bot = bot
        self._user_service = user_service

    @app_commands.command(name="profile")
    async def profile(self, interaction: discord.Interaction) -> None:
        # profile command
        pass

    @app_commands.command(name="ref")
    @app_commands.describe(code="Your unique referral code")
    async def set_referral(self, interaction: discord.Interaction, code: str) -> None:
        # ref command
        pass

    @app_commands.command(name="referrals")
    async def referrals(self, interaction: discord.Interaction) -> None:
        # referrals command
        pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ProfileCog(bot, bot.user_service))
