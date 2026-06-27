import logging

import discord
from discord import Embed, Colour

logger = logging.getLogger("bot")


COMMAND_LABELS = {
    "/add": "📋 Добавление сделки",
    "/profile": "👤 Профиль",
    "/refer": "🔗 Назначение реферала",
    "/referrals": "👥 Рефералы",
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
                        name="\U0001f4dd Детали лога",
                        value="\n".join(lines),
                        inline=False,
                    )
                else:
                    embed.add_field(
                        name="\U0001f4dd Детали лога",
                        value=str(details),
                        inline=False,
                    )

            embed.set_footer(text=f"Успешно" if success else "Ошибка")

            await channel.send(embed=embed)
        except Exception as e:
            logger.warning("audit log failed: %s", e)
