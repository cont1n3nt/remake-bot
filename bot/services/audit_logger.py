import logging

import discord
from discord import Embed, Colour

logger = logging.getLogger("bot")


def _fmt_mention(user: discord.User | discord.Member) -> str:
    """Формат: @Username (ID)"""
    return f"{user.mention} ({user.id})"


COMMAND_LABELS = {
    "/add": "[ /add ] 📋 Добавление сделки",
    "/profile": "[ /profile ] 👤 Профиль",
    "/refer": "[ /refer ] 🔗 Назначение реферала",
    "/referrals": "[ /referrals ] 👥 Рефералы",
    "/tag": "[ /tag ] 💬 Уведомление",
    "/set_rank": "[ /set_rank ] 🏅 Ранг",
    "/set_referral": "[ /set_referral ] 🔗 Реферальная роль",
    "/logs": "[ /logs ] 📜 Логи",
    "/setprice": "[ /setprice ] 🏷 Цена ресурса",
    "/setboost": "[ /setboost ] 🍔 Цена буста",
    "/item_add": "[ /item_add ] ➕ Добавление предмета",
    "/del_item": "[ /del_item ] 🗑 Удаление предмета",
    "/sync_prices": "[ /sync_prices ] 🔄 Синхронизация цен",
    "/give_price": "[ /give_price ] 📥 Выгрузка цен",
    "/price_list": "[ /price_list ] 📋 Прайс-лист",
    "/new_price": "[ /new_price ] 📁 Загрузка цен",
    "/day": "[ /day ] 📊 День",
    "/week": "[ /week ] 📈 Неделя",
    "/month": "[ /month ] 📉 Месяц",
}


class AuditLogger:

    def __init__(self, bot: discord.Client, channel_id: int) -> None:
        self._bot = bot
        self._channel_id = channel_id

    async def log(
        self,
        user: discord.User | discord.Member,
        command: str,
        details: dict | str | None = None,
        success: bool = True,
    ) -> None:
        channel = self._bot.get_channel(self._channel_id)
        if channel is None:
            return

        try:
            title = COMMAND_LABELS.get(command, f"📋 {command}")
            embed = Embed(
                title=title,
                colour=Colour.green() if success else Colour.red(),
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(name="Администратор", value=_fmt_mention(user))

            if details:
                if isinstance(details, dict):
                    clean_lines = []
                    for k, v in details.items():
                        if v is not None and v != "":
                            clean_lines.append(f"**{k}:** {v}")
                    embed.add_field(
                        name="📝 Детали",
                        value="\n".join(clean_lines),
                        inline=False,
                    )
                else:
                    embed.add_field(
                        name="📝 Детали",
                        value=str(details),
                        inline=False,
                    )

            embed.set_footer(text="Связной | Логи")

            await channel.send(embed=embed)
        except Exception as e:
            logger.warning("audit log failed: %s", e)

    async def log_command_usage(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """Глобальное логирование вызова любой слэш-команды (только Embed)."""
        channel = self._bot.get_channel(self._channel_id)
        if channel is None:
            return

        command_name = f"/{interaction.command.name}" if interaction.command else "/unknown"
        user = interaction.user

        try:
            embed = Embed(
                title="Вызов команды",
                colour=Colour.blurple(),
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(name="👤 Пользователь", value=_fmt_mention(user))
            embed.add_field(name="💻 Команда", value=f"`{command_name}`")

            if interaction.data and "options" in interaction.data:
                param_parts = []
                for opt in interaction.data["options"]:
                    if "value" in opt:
                        val = opt["value"]
                        param_parts.append(f"**{opt['name']}:** {val}")
                if param_parts:
                    embed.add_field(name="⚙️ Параметры", value="\n".join(param_parts), inline=False)

            embed.set_footer(text="Связной | Логи")
            await channel.send(embed=embed)
        except Exception as e:
            logger.warning("command usage log failed: %s", e)
