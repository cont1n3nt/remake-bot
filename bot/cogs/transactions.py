import asyncio
import logging
import re

import discord
from discord import app_commands
from discord.ext import commands

from bot.services.sheets_service import SheetsService
from bot.utils.embeds import transaction_confirmation_embed, error_embed

logger = logging.getLogger("bot")

RANK_ROLES: dict[str, int] = {
    "🔹 Standard": 1518324856549277827,
    "🔷 Premium": 1518328036137631805,
    "💠 Prestige": 1518328037631066232,
    "💎 Elite": 1518328222939611166,
    "👑 Legend": 1518328324605083698,
}

REFERRAL_ROLES: dict[str, int] = {
    "🧭 Скаут": 1518583879672270878,
    "📣 Промоутер": 1518584176054636584,
    "🧲 Вербовщик": 1518584268933300274,
    "📢 Амбассадор": 1518584424818671687,
    "🎩 Рекламный Барон": 1518584494410563625,
}


def _role_mention(role_name: str, role_map: dict[str, int]) -> str:
    rid = role_map.get(role_name)
    return f"<@&{rid}>" if rid else role_name


class TransactionsCog(commands.Cog):

    def __init__(self, bot: commands.Bot, sheets_service: SheetsService) -> None:
        self.bot = bot
        self._sheets_service = sheets_service

    @app_commands.command(name="add")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        тип="Тип сделки",
        ник="Ник игрока",
        сумма="Сумма сделки",
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
        await interaction.response.defer()

        nickname = ник.strip()
        referrer = ник_пригласившего.strip() if ник_пригласившего else None

        try:
            cleaned = re.sub(r'\s+', '', сумма)
            cleaned = cleaned.replace("₽", "").replace("руб", "").replace(",", ".")
            amount = float(cleaned)
        except ValueError:
            await interaction.followup.send(embed=error_embed("Некорректная сумма"), ephemeral=True)
            return

        if amount <= 0:
            await interaction.followup.send(embed=error_embed("Сумма должна быть больше 0"), ephemeral=True)
            return

        old_rank = ""
        old_referral_role = ""

        try:
            before = await asyncio.to_thread(self._sheets_service.get_user, nickname)
            if before:
                old_rank = before.rank or ""
        except Exception:
            pass

        if referrer:
            try:
                before_ref = await asyncio.to_thread(self._sheets_service.get_user, referrer)
                if before_ref:
                    old_referral_role = before_ref.referral_role or ""
            except Exception:
                pass

        try:
            await asyncio.to_thread(self._sheets_service.save_transaction, nickname, тип, amount, referrer)
        except Exception as e:
            logger.error("add save error by %s: %s", interaction.user, e)
            await interaction.followup.send(embed=error_embed(f"Ошибка при сохранении: {e}"), ephemeral=True)
            return

        await asyncio.sleep(2)

        await interaction.followup.send(
            embed=transaction_confirmation_embed(nickname, тип, amount, referrer),
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
            after = await asyncio.to_thread(self._sheets_service.get_user, nickname)
            after_ref = await asyncio.to_thread(self._sheets_service.get_user, referrer) if referrer else None

            new_rank = after.rank or "" if after else ""
            new_referral_role = after_ref.referral_role or "" if after_ref else ""

            if new_rank and new_rank != old_rank:
                mention = _role_mention(new_rank, RANK_ROLES)
                if not old_rank:
                    msg = (
                        f"🎉 {nickname}, поздравляем! Вы получили свой первый ранг — {mention}! 🌟\n"
                        "Это отличный старт! Продолжайте копить XP за сделки, совершайте новые операции, и новые вершины не заставят себя ждать! Удачи! 💪\n"
                        "📈 Отслеживать свой прогресс и статистику вы можете в /profile!"
                    )
                else:
                    msg = (
                        f"🔥 {nickname}, невероятный прогресс! Вы достигли нового ранга — {mention}! 🏆\n"
                        "Ваша активность приносит свои плоды. Не останавливайтесь на достигнутом, впереди ещё более крутые награды! Вперёд к новым сделкам! 🚀\n"
                        "📈 Отслеживать свой прогресс и статистику вы можете в /profile!"
                    )
                await interaction.channel.send(msg)

            if referrer and new_referral_role and new_referral_role != old_referral_role:
                mention = _role_mention(new_referral_role, REFERRAL_ROLES)
                msg = (
                    f"👥 Игрок {referrer} получает роль {mention} за приглашение друзей и их активность! 🎉\n"
                    "Спасибо за расширение нашего комьюнити! Приглашайте больше друзей, помогайте им развиваться и забирайте самые сочные реферальные бонусы! 🧲\n"
                    "📈 Отслеживать свою реферальную сеть и статистику вы можете в /profile!"
                )
                await interaction.channel.send(msg)
        except Exception as e:
            logger.warning("rank/referral check failed: %s", e)

        try:
            audit = interaction.client.audit_logger
            tx_label = "Покупка" if тип == "buy" else "Продажа"
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
