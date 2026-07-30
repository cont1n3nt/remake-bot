import logging
from typing import Optional

import discord
from discord import Embed, Colour

logger = logging.getLogger("bot")

# Человекочитаемые заголовки логов. Без скобок, кавычек и служебных символов —
# название самой команды выводится отдельным полем эмбеда (см. _build_embed).
COMMAND_LABELS = {
    "/add": "📋 Добавление сделки",
    "/profile": "👤 Профиль",
    "/refer": "🔗 Назначение реферала",
    "/referrals": "👥 Рефералы",
    "/tag": "💬 Уведомление",
    "/set_rank": "🏅 Ранг",
    "/set_referral": "🔗 Реферальная роль",
    "/logs": "📜 Логи",
    "/setprice": "🏷 Цена ресурса",
    "/setboost": "🚀 Цена буста",
    "/item_add": "➕ Добавление предмета",
    "/del_item": "🗑 Удаление предмета",
    "/sync_prices": "🔄 Синхронизация цен",
    "/give_price": "📥 Выгрузка цен",
    "/price_list": "📋 Прайс-лист",
    "/new_price": "📁 Загрузка цен",
    "/day": "📊 День",
    "/week": "📈 Неделя",
    "/month": "📉 Месяц",
    "/edit_request": "✏️ Изменение заявки",
    "/ticket_form": "📝 Оформление заявки",
}


class AuditLogger:

    # Команды, которые сами вызывают log() с уже читаемыми деталями сделанных
    # изменений — для них log_command_usage() не должен слать второй, менее
    # информативный embed с сырыми параметрами по той же команде.
    _SELF_LOGGED_COMMANDS = {
        "setprice", "setboost", "sync_prices", "set_rank", "set_referral", "add", "new_price",
    }

    def __init__(self, bot: discord.Client, channel_id: int) -> None:
        self._bot = bot
        self._channel_id = channel_id

    def _now_str(self) -> str:
        return discord.utils.utcnow().strftime("%d.%m.%Y • %H:%M")

    @staticmethod
    def _details_text(details: dict | list | str | None) -> str:
        """Свести любой из трёх поддерживаемых видов деталей к одному тексту."""
        if not details:
            return ""
        if isinstance(details, dict):
            return "\n".join(
                f"{k}: {v}" for k, v in details.items() if v is not None and v != ""
            )
        if isinstance(details, list):
            return "\n".join(str(line) for line in details if str(line).strip())
        return str(details).strip()

    def build_embed(
        self,
        user: discord.User | discord.Member,
        command: str,
        details: dict | list | str | None = None,
        success: bool = True,
        image_filename: str | None = None,
    ) -> Embed:
        """Единственный конструктор лог-эмбеда — все логи выглядят одинаково."""
        embed = Embed(
            title=COMMAND_LABELS.get(command, f"📋 {command}"),
            colour=Colour.green() if success else Colour.red(),
        )
        embed.add_field(name="👤 Пользователь", value=f"@{user.name} ({user.id})", inline=False)
        embed.add_field(name="⚙ Команда", value=command, inline=False)

        text = self._details_text(details)
        if text:
            embed.add_field(name="📄 Изменения", value=text[:1024], inline=False)

        embed.add_field(name="🕒 Время", value=self._now_str(), inline=False)
        # Скриншот встраивается внутрь эмбеда, а не висит отдельным вложением.
        if image_filename:
            embed.set_image(url=f"attachment://{image_filename}")
        embed.set_footer(text="Связной | Логи")
        return embed

    async def log(
        self,
        user: discord.User | discord.Member,
        command: str,
        details: dict | list | str | None = None,
        success: bool = True,
        image: discord.File | None = None,
    ) -> Optional[discord.Message]:
        """Отправить лог. Возвращает отправленное сообщение, чтобы вызывающий код
        мог позже отредактировать его вместо отправки второго лога (см. п. 14)."""
        channel = self._bot.get_channel(self._channel_id)
        if channel is None:
            return None

        try:
            embed = self.build_embed(
                user, command, details, success,
                image_filename=image.filename if image else None,
            )
            if image is not None:
                return await channel.send(embed=embed, file=image)
            return await channel.send(embed=embed)
        except Exception as e:
            logger.warning("audit log failed: %s", e)
            return None

    async def edit_log(
        self,
        message_id: int,
        user: discord.User | discord.Member,
        command: str,
        details: dict | list | str | None = None,
        success: bool = True,
        image: discord.File | None = None,
    ) -> bool:
        """Перестроить уже отправленный лог на месте. Используется, когда заявку
        отредактировали: в канале логов должна остаться только итоговая форма."""
        channel = self._bot.get_channel(self._channel_id)
        if channel is None:
            return False
        try:
            message = await channel.fetch_message(message_id)
        except (discord.NotFound, discord.HTTPException) as e:
            logger.debug("audit edit_log: message %s not available: %s", message_id, e)
            return False

        try:
            keep_filename = None
            if image is not None:
                keep_filename = image.filename
            elif message.attachments:
                keep_filename = message.attachments[0].filename

            embed = self.build_embed(
                user, command, details, success, image_filename=keep_filename,
            )
            if image is not None:
                await message.edit(embed=embed, attachments=[image])
            else:
                await message.edit(embed=embed)
            return True
        except discord.HTTPException as e:
            logger.warning("audit edit_log failed for %s: %s", message_id, e)
            return False

    async def log_command_usage(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """Универсальный лог для команд, которые не логируют себя сами (см.
        _SELF_LOGGED_COMMANDS) — переиспользует единственный формат log(),
        чтобы в аудит-канале не было двух разных по виду записей на одну команду."""
        if interaction.command is None:
            return
        command_key = interaction.command.name
        if command_key in self._SELF_LOGGED_COMMANDS:
            return

        command_name = f"/{command_key}"
        details: dict[str, str] = {}
        if interaction.data and "options" in interaction.data:
            for opt in interaction.data["options"]:
                if "value" in opt:
                    details[opt["name"]] = opt["value"]

        await self.log(interaction.user, command_name, details or None)
