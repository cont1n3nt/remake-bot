import discord
from discord import app_commands
from discord.ext import commands

from bot.services.sheets_service import SheetsService


class TransactionModal(discord.ui.Modal, title="Новая сделка"):

    discord_id = discord.ui.TextInput(
        label="Discord ID",
        placeholder="123456789012345678",
    )
    nickname = discord.ui.TextInput(
        label="Nickname",
        placeholder="user#0000",
    )
    amount = discord.ui.TextInput(
        label="Сумма",
        placeholder="100.50",
    )

    def __init__(self, sheets_service: SheetsService) -> None:
        super().__init__()
        self._sheets_service = sheets_service

    async def on_submit(self, interaction: discord.Interaction) -> None:
        pass


class TransactionsCog(commands.Cog):

    def __init__(self, bot: commands.Bot, sheets_service: SheetsService) -> None:
        self.bot = bot
        self._sheets_service = sheets_service

    @app_commands.command(name="tab")
    @app_commands.checks.has_role("Admin")
    async def tab(self, interaction: discord.Interaction) -> None:
        # запись трейда (только для админа)
        await interaction.response.send_modal(TransactionModal(self._sheets_service))

    @tab.error
    async def tab_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TransactionsCog(bot, bot.sheets_service))
