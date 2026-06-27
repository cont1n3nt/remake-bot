import asyncio
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
        placeholder="Ник игрока",
    )
    amount = discord.ui.TextInput(
        label="Сумма",
        placeholder="Сумма",
    )
    referrer = discord.ui.TextInput(
        label="Ник пригласившего",
        placeholder="Ник пригласившего",
        required=False,
    )

    def __init__(self, sheets_service: SheetsService, tx_type: str) -> None:
        super().__init__()
        self._sheets_service = sheets_service
        self._tx_type = tx_type

    async def on_submit(self, interaction: discord.Interaction) -> None:
        tx_type = self._tx_type

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
        referrer = self.referrer.value.strip() or None

        await interaction.response.defer()

        try:
            await asyncio.to_thread(
                self._sheets_service.save_transaction,
                nickname, tx_type, amount, referrer,
            )
        except Exception as e:
            logger.error("add save error by %s: %s", interaction.user, e)
            await interaction.followup.send(
                embed=error_embed(f"Ошибка при сохранении: {e}"),
                ephemeral=True,
            )
            return

        confirm_msg = await interaction.followup.send(
            embed=transaction_confirmation_embed(nickname, tx_type, amount, referrer),
            ephemeral=True,
        )

        await interaction.channel.send(
            "> 🤝 Сделка успешно завершена!\n"
            "> Спасибо, что выбираете «Клондайк Шёпота». Пожалуйста, уделите пару секунд "
            "и оставьте свой честный отзыв в канале <#1490342809075716237>.\n"
            "> \n"
            "> Ваш отзыв — это лучшая поддержка для развития нашего проекта и будущего бота! 💚"
        )

        try:
            await interaction.delete_original_response()
        except Exception:
            pass

        try:
            audit = interaction.client.audit_logger
            tx_label = "Покупка" if tx_type == "buy" else "Продажа"
            details = {
                "Никнейм": nickname,
                "Тип сделки": tx_label,
                "Сумма": str(int(amount)) if amount == int(amount) else str(amount),
            }
            if referrer:
                details["Реферер"] = referrer
            await audit.log(interaction.user, "/add", details)
        except Exception:
            pass


class TransactionView(discord.ui.View):

    def __init__(self, sheets_service: SheetsService) -> None:
        super().__init__()
        self._sheets_service = sheets_service

    @discord.ui.button(label="Покупка", style=discord.ButtonStyle.green)
    async def buy_button(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        await interaction.response.send_modal(
            TransactionModal(self._sheets_service, "buy"),
        )

    @discord.ui.button(label="Продажа", style=discord.ButtonStyle.red)
    async def sell_button(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        await interaction.response.send_modal(
            TransactionModal(self._sheets_service, "sell"),
        )


class TransactionsCog(commands.Cog):

    def __init__(self, bot: commands.Bot, sheets_service: SheetsService) -> None:
        self.bot = bot
        self._sheets_service = sheets_service

    @app_commands.command(name="add")
    @app_commands.checks.has_permissions(administrator=True)
    async def add(self, interaction: discord.Interaction) -> None:
        view = TransactionView(self._sheets_service)
        await interaction.response.send_message(
            "**Выберите тип сделки:**", view=view, ephemeral=True,
        )

    @add.error
    async def add_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        logger.error("add error by %s: %s", interaction.user, error)

        try:
            await interaction.client.audit_logger.log(
                interaction.user, "/add", {"Ошибка": str(error)}, success=False,
            )
        except Exception:
            pass

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
