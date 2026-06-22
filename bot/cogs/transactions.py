import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.services.sheets_service import SheetsService
from bot.utils.embeds import transaction_confirmation_embed, error_embed

logger = logging.getLogger("bot")


class TransactionModal(discord.ui.Modal, title="Новая сделка"):

    nickname = discord.ui.TextInput(
        label="Ник",
        placeholder="Хемуль",
    )
    tx_type = discord.ui.TextInput(
        label="Тип",
        placeholder="buy или sell",
    )
    amount = discord.ui.TextInput(
        label="Сумма",
        placeholder="625000",
    )

    def __init__(self, sheets_service: SheetsService) -> None:
        super().__init__()
        self._sheets_service = sheets_service

    async def on_submit(self, interaction: discord.Interaction) -> None:
        tx_type = self.tx_type.value.strip().lower()
        if tx_type not in ("buy", "sell"):
            await interaction.response.send_message(
                embed=error_embed("Тип должен быть buy или sell"),
                ephemeral=True,
            )
            return

        try:
            amount = float(self.amount.value.replace(" ", ""))
        except ValueError:
            await interaction.response.send_message(
                embed=error_embed("Некорректная сумма"),
                ephemeral=True,
            )
            return

        if amount <= 0:
            await interaction.response.send_message(
                embed=error_embed("Сумма должна быть больше 0"),
                ephemeral=True,
            )
            return

        nickname = self.nickname.value.strip()
        try:
            self._sheets_service.ensure_user(nickname)
            self._sheets_service.save_transaction(nickname, tx_type, amount)
        except Exception as e:
            logger.error("tab save error: %s", e)
            await interaction.response.send_message(
                embed=error_embed(f"Ошибка при сохранении: {e}"),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            embed=transaction_confirmation_embed(nickname, tx_type, amount),
        )


class TransactionsCog(commands.Cog):

    def __init__(self, bot: commands.Bot, sheets_service: SheetsService) -> None:
        self.bot = bot
        self._sheets_service = sheets_service

    @app_commands.command(name="tab")
    @app_commands.checks.has_permissions(administrator=True)
    async def tab(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(TransactionModal(self._sheets_service))

    @tab.error
    async def tab_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        logger.error("tab error: %s", error)

        if isinstance(error, app_commands.MissingPermissions):
            text = "Недостаточно прав. Требуются права администратора."
        else:
            text = f"Ошибка: {error}"

        try:
            await interaction.response.send_message(
                embed=error_embed(text), ephemeral=True,
            )
        except discord.errors.InteractionResponded:
            await interaction.followup.send(
                embed=error_embed(text), ephemeral=True,
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TransactionsCog(bot, bot.sheets_service))
