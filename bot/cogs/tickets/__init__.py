"""Ког для автоматизации тикетов: пошаговый Wizard UI, скриншоты через attachment, редактирование заявок."""

import asyncio
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot.services.ocr_service import OcrService, _fmt
from bot.config.constants import MONITORED_CHANNELS, CATEGORY_CHANNELS
from bot.cogs.tickets.views_delivery import TicketFormView
from bot.cogs.tickets.views_edit import EditRequestView
from bot.cogs.tickets.storage import _load_request_meta_by_channel, _save_deal_report

logger = logging.getLogger("bot")

TICKET_TOOL_BOT_ID = 557628352828014614


# ------------------------------------------------------------------ #
#  Основной ког                                                      #
# ------------------------------------------------------------------ #

class TicketCog(commands.Cog):

    def __init__(self, bot: commands.Bot, ocr_service: OcrService, audit_logger) -> None:
        self.bot = bot
        self.ocr = ocr_service
        self.audit_logger = audit_logger
        self._view = TicketFormView()
        self._edit_view = EditRequestView()
        bot.add_view(self._view)
        bot.add_view(self._edit_view)
        self._sending_locks: set[int] = set()

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        if not isinstance(channel, discord.TextChannel):
            return
        if channel.category_id not in CATEGORY_CHANNELS.values():
            return
        category_name = next((n for n, cid in CATEGORY_CHANNELS.items() if cid == channel.category_id), None)
        if category_name is None:
            return

        def ticket_tool_check(m: discord.Message) -> bool:
            return (m.author.id == TICKET_TOOL_BOT_ID and m.channel.id == channel.id)

        try:
            await self.bot.wait_for("message", timeout=60.0, check=ticket_tool_check)
        except asyncio.TimeoutError:
            logger.warning("Ticket Tool message not received in %s, sending form anyway", channel.id)

        await self._send_form_to_channel(channel, category_name)

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread) -> None:
        parent = thread.parent
        if parent is None:
            return
        if isinstance(parent, discord.ForumChannel):
            category_name = next((n for n, cid in CATEGORY_CHANNELS.items() if cid == parent.id), None)
            if category_name:
                await self._send_form_to_channel(thread, category_name)
            return
        if parent.category_id is None or parent.category_id not in CATEGORY_CHANNELS.values():
            return
        category_name = next((n for n, cid in CATEGORY_CHANNELS.items() if cid == parent.category_id), None)
        if category_name is None:
            return

        def ticket_tool_check(m: discord.Message) -> bool:
            return (m.author.id == TICKET_TOOL_BOT_ID and m.channel.id == thread.id)

        try:
            await self.bot.wait_for("message", timeout=60.0, check=ticket_tool_check)
        except asyncio.TimeoutError:
            logger.warning("Ticket Tool message not received in thread %s, sending form anyway", thread.id)

        await self._send_form_to_channel(thread, category_name)

    def _build_form_embed(self, category: str) -> discord.Embed:
        if "Заказ" in category:
            description = (
                "Для заказа бустов нажмите кнопку ниже и заполните форму.\n"
                "Вы сможете выбрать бусты и указать их количество."
            )
        else:
            description = (
                "Для продажи нажмите кнопку ниже и заполните форму.\n"
                "Вы также можете прикрепить скриншот — бот автоматически "
                "распознает предметы и рассчитает сумму."
            )
        return discord.Embed(
            title=f"📋 {category}",
            description=description,
            colour=discord.Colour.blurple(),
        )

    async def _find_existing_form_message(self, channel) -> Optional[discord.Message]:
        """Guard against a duplicate form send if this channel already got one
        (e.g. a redelivered gateway event for the same ticket channel)."""
        try:
            async for msg in channel.history(limit=50):
                if msg.author == self.bot.user and msg.components:
                    for comp in msg.components:
                        for child in comp.children:
                            if child.custom_id == "ticket_form:open":
                                return msg
        except Exception as e:
            logger.debug("_find_existing_form_message failed for channel %s: %s", channel.id, e)
        return None

    async def _send_form_to_channel(self, channel, category: str) -> None:
        ch_id = channel.id
        if ch_id in self._sending_locks:
            logger.debug("Already sending form to channel %s, skipping", ch_id)
            return
        self._sending_locks.add(ch_id)
        try:
            existing = await self._find_existing_form_message(channel)
            if existing is not None:
                logger.debug("Form already present in channel %s, skipping duplicate send", ch_id)
                return
            embed = self._build_form_embed(category)
            await channel.send(embed=embed, view=self._view)
        except Exception as e:
            logger.warning("Не удалось отправить форму в %s: %s", channel.id, e)
        finally:
            self._sending_locks.discard(ch_id)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        category_id = getattr(message.channel, "category_id", None)
        is_ticket = category_id is not None and category_id in CATEGORY_CHANNELS.values()
        is_monitored = message.channel.id in MONITORED_CHANNELS
        if not is_ticket and not is_monitored:
            return
        images = [a for a in message.attachments if a.content_type and a.content_type.startswith("image/")]
        if not images:
            return
        await message.add_reaction("⏳")
        for attachment in images:
            await self._process_image(message, attachment)
        if is_ticket:
            await self._attach_screenshot_to_request(message, images[0])
        await message.remove_reaction("⏳", self.bot.user)
        await message.add_reaction("✅")

    async def _attach_screenshot_to_request(self, message: discord.Message, attachment: discord.Attachment) -> None:
        """Если в этом канале уже опубликована заявка — встраиваем скриншот в её
        Embed через set_image (attachment), а не оставляем отдельным вложением."""
        meta = await asyncio.to_thread(_load_request_meta_by_channel, message.channel.id)
        if meta is None:
            return
        request_message_id, _data = meta
        try:
            request_msg = await message.channel.fetch_message(request_message_id)
        except (discord.NotFound, discord.HTTPException):
            return
        if not request_msg.embeds:
            return
        embed = request_msg.embeds[0]
        try:
            fp = await attachment.to_file()
        except Exception:
            return
        embed.set_image(url=f"attachment://{fp.filename}")
        try:
            await request_msg.edit(embed=embed, attachments=[fp])
        except (discord.HTTPException, discord.Forbidden) as e:
            logger.warning("Не удалось прикрепить скриншот к заявке %s: %s", request_message_id, e)

    async def _process_image(self, message: discord.Message, attachment: discord.Attachment) -> None:
        try:
            await self.ocr.reload_prices()
            img_bytes = await attachment.read()
            text = await asyncio.wait_for(self.ocr.extract_text(img_bytes), timeout=30.0)
            text = text.strip()
            if not text:
                logger.info("OCR не нашёл текст на изображении %s", attachment.filename)
                return
            parsed = self.ocr.parse_items(text)
            readable, total, unknown = self.ocr.cross_reference(parsed)
            items_str = "; ".join(readable)
            result = f"[OCR Result] Items: {items_str} | Total: {_fmt(total)} RUB"
            entry = {
                "type": "ocr", "timestamp": discord.utils.utcnow().isoformat(),
                "user_id": message.author.id, "user_name": str(message.author),
                "message_id": message.id, "filename": attachment.filename,
                "items": parsed, "total": total, "unknown": unknown, "raw_text": text,
            }
            await _save_deal_report(message.channel.id, entry)
            await message.channel.send(f"```{result}```")
            if unknown:
                await message.channel.send(
                    f"⚠️ Не удалось определить цену для: {', '.join(unknown)}. "
                    "Проверьте базу цен или укажите стоимость вручную."
                )
        except asyncio.TimeoutError:
            await message.channel.send("⏱ OCR-распознавание превысило таймаут (30 с).")
        except ValueError as exc:
            await message.channel.send(f"⚠️ {exc}")
        except Exception:
            logger.exception("Ошибка OCR при обработке %s", attachment.filename)
            await message.channel.send("⚠️ Произошла ошибка при OCR-распознавании.")

    @app_commands.command(name="tag", description="💬 (Админ) Отправить уведомление пользователю в личные сообщения о тикете")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(user="Уведомить участника о тикете в личных сообщениях")
    async def tag(self, interaction: discord.Interaction, user: discord.User) -> None:
        await interaction.response.defer(ephemeral=True)
        embed = discord.Embed(
            title="📢 Уведомление по тикету",
            description=(
                f"Здравствуйте, {user.mention}!\n"
                f"В вашем активном тикете {interaction.channel.mention} поступило "
                f"новое сообщение. Команда ожидает вашего ответа, чтобы продолжить "
                f"сделку или решить вопрос.\n"
                f"Пожалуйста, вернитесь в чат, когда будете готовы!"
            ),
            colour=discord.Color.brand_green(),
        )
        embed.add_field(name="🔮 От Главы Шёпота", value="Команда «Клондайк Шёпота»", inline=False)
        icon = interaction.guild.icon.url if interaction.guild and interaction.guild.icon else None
        embed.set_footer(text="Маркетплейс «Клондайк Шёпота»", icon_url=icon)
        embed.timestamp = discord.utils.utcnow()
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="🔗 Перейти к тикету", url=interaction.channel.jump_url, style=discord.ButtonStyle.link))
        try:
            await user.send(embed=embed, view=view)
            await interaction.followup.send(f"✅ Уведомление отправлено пользователю {user.mention}.", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("Не удалось отправить сообщение пользователю.", ephemeral=True)

    @tag.error
    async def tag_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            text = "Недостаточно прав. Требуются права администратора."
        else:
            text = f"Ошибка: {error}"
        try:
            await interaction.response.send_message(text, ephemeral=True)
        except discord.errors.InteractionResponded:
            await interaction.followup.send(text, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    ocr_service = OcrService()
    await bot.add_cog(TicketCog(bot, ocr_service, bot.audit_logger))
