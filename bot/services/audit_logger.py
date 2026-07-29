import logging
from datetime import datetime, timezone, timedelta

import discord
from discord import Embed, Colour

logger = logging.getLogger("bot")


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
            )
            embed.add_field(name="\U0001f464 Пользователь", value=f"{user.mention} (`{user.id}`)")

            if details:
                if isinstance(details, dict):
                    lines = []
                    for k, v in details.items():
                        if v is not None and v != "":
                            lines.append(f"└ {k}: {v}")
                    embed.add_field(
                        name="\U0001f4dd Детали",
                        value="\n".join(lines),
                        inline=False,
                    )
                else:
                    embed.add_field(
                        name="\U0001f4dd Детали",
                        value=str(details),
                        inline=False,
                    )

            embed.set_footer(text="Успешно" if success else "Ошибка")

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
        now = (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%d.%m.%y %H:%M")

        params = []
        if interaction.data and "options" in interaction.data:
            for opt in interaction.data["options"]:
                if "value" in opt:
                    params.append(f"{opt['name']}={opt['value']}")

        params_str = ", ".join(params) if params else ""
        role_label = "Админ" if isinstance(user, discord.Member) and user.guild_permissions.administrator else "Игрок"

        try:
            embed = Embed(
                title="Вызов команды",
                colour=Colour.blurple(),
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(name="Пользователь", value=f"{user.mention} (`{user.id}`)")
            embed.add_field(name="Команда", value=f"`{command_name}`")
            if params_str:
                embed.add_field(name="Параметры", value=params_str, inline=False)
            embed.set_footer(text=f"{role_label} • {now}")
            await channel.send(embed=embed)
        except Exception as e:
            logger.warning("command usage log failed: %s", e)
