import asyncio
import logging
import math

import discord
from discord import app_commands
from discord.ext import commands

from bot.config.constants import RANK_ROLES_BY_LABEL, REFERRAL_ROLES_BY_LABEL
from bot.services.sheets_service import SheetsService
from bot.utils.embeds import error_embed
from bot.utils.calculator import safe_calc

logger = logging.getLogger("bot")

_MAX_NICK_LENGTH = 32


def _role_mention(role_name: str, role_map: dict[str, int]) -> str:
    rid = role_map.get(role_name)
    return f"<@&{rid}>" if rid else role_name


def _amount_str(amount: float) -> str:
    if not math.isfinite(amount):
        return str(amount)
    return str(int(amount)) if amount == int(amount) else str(amount)


class TransactionsCog(commands.Cog):

    def __init__(self, bot: commands.Bot, sheets_service: SheetsService) -> None:
        self.bot = bot
        self._sheets_service = sheets_service

    @app_commands.command(name="add", description="📝 (Админ) Записать новую сделку в Google Таблицу (сумма, тип, ник) с авто-калькулятором")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        тип="Тип сделки",
        ник="Ник игрока",
        сумма="Сумма сделки (поддерживает выражения: 727 540 + 110 222)",
        ник_пригласившего="Ник того, кто пригласил игрока (необязательно)",
    )
    @app_commands.choices(тип=[
        app_commands.Choice(name="Покупка", value="buy"),
        app_commands.Choice(name="Продажа", value="sell"),
    ])
    async def add(
        self,
        interaction: discord.Interaction,
        тип: str,
        ник: str,
        сумма: str,
        ник_пригласившего: str | None = None,
    ) -> None:
        if not interaction.response.is_done():
            try:
                await interaction.response.defer(ephemeral=True)
            except Exception:
                pass

        nickname = ник.strip().lower()
        if not nickname:
            await interaction.followup.send(embed=error_embed("Ник не может быть пустым."), ephemeral=True)
            return
        if len(nickname) > _MAX_NICK_LENGTH:
            await interaction.followup.send(
                embed=error_embed(f"Ник слишком длинный (максимум {_MAX_NICK_LENGTH} символов)."), ephemeral=True,
            )
            return

        referrer = ник_пригласившего.strip().lower() if ник_пригласившего else None
        if referrer:
            if referrer == nickname:
                await interaction.followup.send(embed=error_embed("Нельзя указывать самого себя."), ephemeral=True)
                return

        logger.debug("add input: сумма=%r ник=%r реферер=%r", сумма, nickname, referrer)
        try:
            amount = safe_calc(сумма)
        except Exception as e:
            logger.warning("add safe_calc failed: сумма=%r error=%s", сумма, e)
            await interaction.followup.send(embed=error_embed("Некорректное выражение в сумме."), ephemeral=True)
            return

        if amount <= 0:
            await interaction.followup.send(embed=error_embed("Сумма должна быть больше 0"), ephemeral=True)
            return

        await self.record_transaction(interaction, тип, nickname, amount, referrer)

    async def record_transaction(
        self,
        interaction: discord.Interaction,
        tx_type: str,
        nickname: str,
        amount: float,
        referrer: str | None = None,
    ) -> None:
        """Единая логика фиксации сделки: используется и командой /add, и кнопкой
        «✅ Подтвердить» под заявкой в тикете — чтобы поведение никогда не расходилось."""
        old_rank = ""
        old_referral_role = ""

        try:
            before = await asyncio.to_thread(self._sheets_service.get_user, nickname)
            if before:
                old_rank = before.rank or ""
        except Exception as e:
            logger.warning("record_transaction: failed to read 'before' state for %s: %s", nickname, e)

        if referrer:
            try:
                before_ref = await asyncio.to_thread(self._sheets_service.get_user, referrer)
                if before_ref:
                    old_referral_role = before_ref.referral_role or ""
            except Exception as e:
                logger.warning("record_transaction: failed to read 'before' referral state for %s: %s", referrer, e)

        try:
            await asyncio.to_thread(self._sheets_service.ensure_user, nickname)
            if referrer:
                await asyncio.to_thread(self._sheets_service.ensure_user, referrer)
        except Exception as e:
            logger.warning("record_transaction: ensure_user failed for %s/%s: %s", nickname, referrer, e)

        try:
            await asyncio.to_thread(self._sheets_service.save_transaction, nickname, tx_type, amount, referrer)
        except Exception as e:
            logger.error("record_transaction save error by %s: %s", interaction.user, e)
            await interaction.followup.send(embed=error_embed(f"Ошибка при сохранении: {e}"), ephemeral=True)
            return

        new_rank = ""
        new_referral_role = ""
        for attempt in range(5):
            try:
                after = await asyncio.to_thread(self._sheets_service.get_user, nickname)
                if after:
                    new_rank = after.rank or ""
                    if referrer:
                        after_ref = await asyncio.to_thread(self._sheets_service.get_user, referrer)
                        new_referral_role = after_ref.referral_role or "" if after_ref else ""
                    break
            except Exception as e:
                logger.debug("record_transaction: 'after' state poll attempt %s failed for %s: %s", attempt, nickname, e)
            if attempt < 4:
                await asyncio.sleep(1)

        embed = discord.Embed(
            title="Сделка зафиксирована",
            colour=discord.Colour.green(),
        )
        embed.add_field(name="Ник", value=nickname)
        embed.add_field(name="Тип", value="Покупка" if tx_type == "buy" else "Продажа")
        embed.add_field(name="Сумма", value=_amount_str(amount))
        if referrer:
            embed.add_field(name="Ник пригласившего", value=referrer)
        await interaction.followup.send(embed=embed, ephemeral=True)

        await interaction.channel.send(
            "> 🤝 Сделка успешно завершена!\n"
            "> Спасибо, что выбираете «Клондайк Шёпота». Пожалуйста, уделите пару секунд "
            "и оставьте свой честный отзыв в канале <#1490342809075716237>.\n"
            "> \n"
            "> Ваш отзыв — это лучшая поддержка для развития нашего проекта и будущего бота!"
        )

        try:
            if new_rank and new_rank != old_rank:
                role_mention = _role_mention(new_rank, RANK_ROLES_BY_LABEL)
                msg = (
                    f"🎉 **Выдача ранговой роли!**\n\n"
                    f"1️⃣ Пользователь: {interaction.user.mention}\n"
                    f"2️⃣ Игровой никнейм: `{nickname}`\n"
                    f"3️⃣ Полученная роль: {role_mention}\n\n"
                    f"Поздравляем с получением новой роли! 🌟"
                )
                await interaction.channel.send(msg)

            if referrer and new_referral_role and new_referral_role != old_referral_role:
                role_mention = _role_mention(new_referral_role, REFERRAL_ROLES_BY_LABEL)
                msg = (
                    f"🎉 **Выдача реферальной роли!**\n\n"
                    f"1️⃣ Пользователь: {interaction.user.mention}\n"
                    f"2️⃣ Игровой никнейм: `{referrer}`\n"
                    f"3️⃣ Полученная роль: {role_mention}\n\n"
                    f"Поздравляем с получением новой роли! 🌟"
                )
                await interaction.channel.send(msg)
        except Exception as e:
            logger.warning("rank/referral check failed: %s", e)

        try:
            audit = interaction.client.audit_logger
            details = {
                "Ник": nickname,
                "Тип": "Покупка" if tx_type == "buy" else "Продажа",
                "Сумма": f"{_amount_str(amount)} ₽",
            }
            if referrer:
                details["Пригласил"] = referrer
            await audit.log(interaction.user, "/add", details)
        except Exception:
            pass

    @add.error
    async def add_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        logger.error("add error by %s: %s", interaction.user, error)
        try:
            await interaction.client.audit_logger.log(interaction.user, "/add", {"Ошибка": str(error)}, success=False)
        except Exception:
            pass
        if isinstance(error, app_commands.MissingPermissions):
            text = "Недостаточно прав. Требуются права администратора."
        else:
            text = f"Ошибка: {error}"
        try:
            await interaction.response.send_message(embed=error_embed(text), ephemeral=True)
        except discord.errors.InteractionResponded:
            await interaction.followup.send(embed=error_embed(text), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TransactionsCog(bot, bot.sheets_service))
