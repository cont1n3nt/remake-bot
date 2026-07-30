"""Ког для автоматизации тикетов: пошаговый Wizard UI, скриншоты через attachment, редактирование заявок."""

import asyncio
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot.services.ocr_service import OcrService, _fmt
from bot.config.constants import MONITORED_CHANNELS, CATEGORY_CHANNELS
from bot.cogs.tickets.embeds import _build_request_card_embed
from bot.cogs.tickets.views_delivery import TicketFormView
from bot.cogs.tickets.views_boosts import BoostSelectionView
from bot.cogs.tickets.storage import (
    form_store,
    _save_request_meta,
    _load_request_meta,
    _load_request_meta_by_channel,
    _save_deal_report,
)

logger = logging.getLogger("bot")

TICKET_TOOL_BOT_ID = 557628352828014614


# ------------------------------------------------------------------ #
#  Кнопка изменения заявки                                           #
# ------------------------------------------------------------------ #

class EditRequestModal(discord.ui.Modal):

    def __init__(self, message_id: int, request_data: dict):
        super().__init__(title="✏️ Изменить заявку", timeout=300)
        self.message_id = message_id
        self.request_data = request_data
        text_data = request_data.get("text_data", {})

        delivery_current = request_data.get("delivery_method", "Почта")
        self.add_item(discord.ui.TextInput(
            label="Способ получения (Почта или Трейд)",
            custom_id="delivery_method",
            required=True,
            style=discord.TextStyle.short,
            placeholder="Почта или Трейд",
            default=delivery_current,
        ))

        self.add_item(discord.ui.TextInput(
            label="Игровой ник",
            custom_id="game_nick",
            required=True,
            style=discord.TextStyle.short,
            default=text_data.get("game_nick", ""),
        ))

        if "Заказ" in request_data.get("category", ""):
            self.add_item(discord.ui.TextInput(
                label="До какой даты выполнить",
                custom_id="deadline",
                required=True,
                style=discord.TextStyle.short,
                placeholder="ДД.ММ.ГГГГ ЧЧ:ММ",
                default=text_data.get("deadline", ""),
            ))

        self.add_item(discord.ui.TextInput(
            label="Кто пригласил (игра)",
            custom_id="referrer_game",
            required=False,
            style=discord.TextStyle.short,
            default=text_data.get("referrer_game", ""),
        ))

        self.add_item(discord.ui.TextInput(
            label="Кто пригласил (Discord)",
            custom_id="referrer_discord",
            required=False,
            style=discord.TextStyle.short,
            default=text_data.get("referrer_discord", ""),
        ))

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        text_data = {}
        delivery_method = ""
        for child in self.children:
            if isinstance(child, discord.ui.TextInput):
                if child.custom_id == "delivery_method":
                    delivery_method = child.value.strip()
                else:
                    text_data[child.custom_id] = child.value

        self.request_data["text_data"] = text_data
        self.request_data["delivery_method"] = delivery_method

        category = self.request_data.get("category", "")

        if "Заказ" in category:
            form_store.set(interaction.user.id, "edit_message_id", self.message_id)
            form_store.set(interaction.user.id, "edit_request_data", self.request_data)
            form_store.set(interaction.user.id, "text_data", text_data)
            form_store.set(interaction.user.id, "delivery_method", delivery_method)
            form_store.set(interaction.user.id, "category", category)
            boosts = self.request_data.get("selected_boosts", [])
            form_store.set(interaction.user.id, "selected_boosts", boosts)

            selected = [b["name"] for b in boosts]
            view = await BoostSelectionView.create(interaction, selected)
            await interaction.followup.send("**Выберите нужные бусты:**", view=view, ephemeral=True)
        else:
            await self._update_embed(interaction)

    async def _update_embed(self, interaction: discord.Interaction):
        text_data = self.request_data.get("text_data", {})
        delivery = self.request_data.get("delivery_method", "")
        boosts = self.request_data.get("selected_boosts", [])
        total_price = self.request_data.get("total_price", 0.0)
        category = self.request_data.get("category", "")

        embed = _build_request_card_embed(interaction, text_data, delivery, boosts, total_price, category)

        await _save_request_meta(interaction.channel_id, self.message_id, interaction.user.id, self.request_data)

        try:
            msg = await interaction.channel.fetch_message(self.message_id)
            files = []
            for a in msg.attachments:
                if a.content_type and a.content_type.startswith("image/"):
                    fp = await a.to_file()
                    files.append(fp)
            kwargs = {"embed": embed, "view": EditRequestView()}
            if files:
                kwargs["attachments"] = files[:1]
            await msg.edit(**kwargs)
        except (discord.NotFound, discord.HTTPException) as e:
            await interaction.followup.send("⚠️ Не удалось найти заявку для редактирования.", ephemeral=True)
            logger.warning("Edit failed: message %s not found: %s", self.message_id, e)
            return

        await interaction.followup.send("✅ Заявка обновлена.", ephemeral=True)

        try:
            audit = interaction.client.audit_logger
            await audit.log(interaction.user, "/edit_request", {
                "Категория": category,
                "Ник в игре": text_data.get("game_nick", "") or "—",
                "Способ": delivery or "—",
            })
        except Exception:
            pass


class EditRequestView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="✏️ Изменить заявку", style=discord.ButtonStyle.secondary, custom_id="edit_request")
    async def edit_callback(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if not interaction.message:
            await interaction.response.send_message("Не удалось определить заявку.", ephemeral=True)
            return
        message_id = interaction.message.id
        meta = _load_request_meta(message_id)
        if meta is None:
            await interaction.response.send_message(
                "Не удалось загрузить данные заявки. Возможно, она была создана до перезапуска бота.",
                ephemeral=True,
            )
            return
        if meta.get("user_id") != interaction.user.id:
            await interaction.response.send_message("Это не ваша заявка.", ephemeral=True)
            return
        await interaction.response.send_modal(EditRequestModal(message_id, meta["data"]))

    @discord.ui.button(label="✅ Подтвердить", style=discord.ButtonStyle.success, custom_id="confirm_request")
    async def confirm_callback(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if not interaction.message:
            await interaction.response.send_message("Не удалось определить заявку.", ephemeral=True)
            return
        message_id = interaction.message.id
        meta = _load_request_meta(message_id)
        if meta is None:
            await interaction.response.send_message(
                "Не удалось загрузить данные заявки.", ephemeral=True,
            )
            return
        request_data = meta["data"]
        await interaction.response.send_modal(ConfirmModal(message_id, request_data))


# ------------------------------------------------------------------ #
#  Modal подтверждения заявки (для администратора)                   #
# ------------------------------------------------------------------ #

class ConfirmModal(discord.ui.Modal):

    def __init__(self, message_id: int, request_data: dict):
        super().__init__(title="✅ Подтверждение сделки", timeout=120)
        self.message_id = message_id
        self.request_data = request_data

        self.add_item(discord.ui.TextInput(
            label="Сумма сделки",
            custom_id="amount",
            required=True,
            style=discord.TextStyle.short,
            placeholder="Введите сумму сделки",
        ))

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        raw_amount = self.children[0].value if self.children else ""
        try:
            from bot.utils.calculator import safe_calc
            amount = safe_calc(raw_amount)
        except Exception:
            await interaction.followup.send("Некорректная сумма.", ephemeral=True)
            return

        if amount <= 0:
            await interaction.followup.send("Сумма должна быть больше 0.", ephemeral=True)
            return

        text_data = self.request_data.get("text_data", {})
        category = self.request_data.get("category", "")
        nick = text_data.get("game_nick", "").strip().lower()
        referrer_game = text_data.get("referrer_game", "").strip().lower()

        if not nick:
            await interaction.followup.send("В заявке не указан игровой ник.", ephemeral=True)
            return

        # Игрок продаёт боту (категория "Продажа ...") -> бот покупает -> tx_type="buy"
        # Игрок заказывает/покупает у бота (категория "Заказ ...") -> бот продаёт -> tx_type="sell"
        tx_type = "buy" if "Продажа" in category else "sell"

        transactions_cog = interaction.client.get_cog("TransactionsCog")
        if transactions_cog is None:
            await interaction.followup.send("⚠️ Модуль сделок недоступен.", ephemeral=True)
            return

        # Полностью переиспользуем логику /add (ensure_user, ранги, реферальные роли,
        # сообщение с просьбой оставить отзыв, единый аудит-лог) — без дублирования кода.
        await transactions_cog.record_transaction(interaction, tx_type, nick, amount, referrer_game or None)


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
            pass

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
        except Exception:
            pass
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
