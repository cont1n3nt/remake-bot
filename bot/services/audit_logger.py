import logging
from datetime import datetime

import discord
from discord import Embed, Colour

logger = logging.getLogger("bot")


class AuditLogger:

    def __init__(self, bot: discord.Client, channel_id: int) -> None:
        self._bot = bot
        self._channel_id = channel_id

    async def log(
        self,
        user: discord.User | discord.Member,
        command: str,
        details: str = "",
        success: bool = True,
    ) -> None:
        channel = self._bot.get_channel(self._channel_id)
        if channel is None:
            return

        try:
            embed = Embed(
                title=f"📋 {command}",
                colour=Colour.green() if success else Colour.red(),
                timestamp=datetime.now(),
            )
            embed.set_author(
                name=f"{user} ({user.id})",
                icon_url=user.display_avatar.url,
            )
            embed.add_field(name="User", value=user.mention, inline=False)
            if details:
                embed.add_field(name="Details", value=details, inline=False)

            await channel.send(embed=embed)
        except Exception as e:
            logger.warning("audit log failed: %s", e)
