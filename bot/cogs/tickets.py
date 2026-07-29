"""Ког для автоматизации тикетов: многошаговая форма (Select → Modal → бусты), OCR, /tag."""

import asyncio
import json
import logging
import os
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot.services.ocr_service import OcrService, _fmt
from bot.config.constants import MONITORED_CHANNELS, CATEGORY_CHANNELS
from bot.utils.embeds import resolve_emoji

logger = logging.getLogger("bot")

DEAL_REPORTS_DIR = "deal_reports"
TICKET_TOOL_BOT_ID = 557628352828014614

# ------------------------------------------------------------------ #
#  Хранилище временных данных формы (в памяти)                       #
# ------------------------------------------------------------------ #

class FormDataStore:
    def __init__(self):
        self._data: dict[int, dict] = {}

    def get(self, user_id: int) -> dict:
        return self._data.setdefault(user_id, {})

    def set(self, user_id: int, key: str, value):
        self._data.setdefault(user_id, {})[key] = value

    def clear(self, user_id: int):
        self._data.pop(user_id, None)

form_store = FormDataStore()

# ------------------------------------------------------------------ #
#  View: выбор способа продажи/покупки (Почта / Трейд)               #
# ------------------------------------------------------------------ #

class DeliveryMethodSelect(discord.ui.Select):

    def __init__(self, category: str):
        options = [
            discord.SelectOption(label="Почта", emoji="📮"),
            discord.SelectOption(label="Трейд", emoji="🤝"),
        ]
        placeholder = "Выберите способ продажи" if "Продажа" in category else "Выберите способ покупки"
        super().__init__(placeholder=placeholder, options=options, custom_id=f"delivery_{category[:4]}")
        self.category = category

    async def callback(self, interaction: discord.Interaction):
        form_store.set(interaction.user.id, "delivery_method", self.values[0])
        if self.values[0] == "Почта":
            if "Продажа" in self.category:
                form_store.set(interaction.user.id, "delivery_text", "Ник: Scaryyyyy")
            else:
                form_store.set(interaction.user.id, "delivery_text", "нужно сразу скинуть деньги на ник Scaryyyyy")
        modal = TicketFormModal(self.category)
        await interaction.response.send_modal(modal)


class DeliveryMethodView(discord.ui.View):
    def __init__(self, category: str):
        super().__init__(timeout=120)
        self.add_item(DeliveryMethodSelect(category))


# ------------------------------------------------------------------ #
#  Модальное окно с текстовыми полями                                #
# ------------------------------------------------------------------ #

class TicketFormModal(discord.ui.Modal):

    def __init__(self, category: str):
        title = "Форма — Продажа" if "Продажа" in category else "Форма — Заказ бустов"
        super().__init__(title=title, timeout=300)
        self.category = category

        self.add_item(discord.ui.TextInput(
            label="Ваш ник в игре",
            custom_id="game_nick",
            required=True,
            style=discord.TextStyle.short,
            placeholder="Введите ваш игровой никнейм",
        ))

        if "Заказ" in category:
            self.add_item(discord.ui.TextInput(
                label="До какой даты и времени нужно сделать",
                custom_id="deadline",
                required=True,
                style=discord.TextStyle.short,
                placeholder="ДД.ММ.ГГГГ ЧЧ:ММ",
            ))

        if "Продажа" in category:
            self.add_item(discord.ui.TextInput(
                label="Ссылка на скриншот (необязательно)",
                custom_id="screenshot_url",
                required=False,
                style=discord.TextStyle.short,
                placeholder="https://i.imgur.com/ваш_скриншот.png",
            ))

        self.add_item(discord.ui.TextInput(
            label="Кто вас пригласил (Ник в игре)",
            custom_id="referrer_game",
            required=False,
            style=discord.TextStyle.short,
        ))

        self.add_item(discord.ui.TextInput(
            label="Кто вас пригласил (Ник в Discord)",
            custom_id="referrer_discord",
            required=False,
            style=discord.TextStyle.short,
        ))

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        data = {}
        for child in self.children:
            if isinstance(child, discord.ui.TextInput):
                data[child.custom_id] = child.value
        form_store.set(interaction.user.id, "text_data", data)
        form_store.set(interaction.user.id, "category", self.category)

        if "Заказ" in self.category:
            view = await BoostSelectView.create(interaction)
            content = "Выберите нужные бусты, затем нажмите ✅ **Подтвердить**."
            await interaction.followup.send(content, view=view, ephemeral=True)
        else:
            await self._finalize(interaction)

    async def _finalize(self, interaction: discord.Interaction):
        store = form_store.get(interaction.user.id)
        text_data = store.get("text_data", {})
        delivery = store.get("delivery_method", "")
        delivery_text = store.get("delivery_text", "")
        category = store.get("category", "")
        boosts = store.get("selected_boosts", [])

        embed = discord.Embed(
            title=f"📋 Новая заявка — {category}",
            colour=discord.Colour.green(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        embed.add_field(name="👤 Создатель", value=interaction.user.mention)
        embed.add_field(name="🎮 Ник в игре", value=text_data.get("game_nick", "—"))

        if delivery:
            method_str = f"📮 Почта ({delivery_text})" if delivery == "Почта" else "🤝 Трейд"
            embed.add_field(name="Способ", value=method_str)

        if text_data.get("deadline", ""):
            embed.add_field(name="⏰ До даты и времени", value=text_data["deadline"])

        ref_game = text_data.get("referrer_game", "").strip()
        ref_discord = text_data.get("referrer_discord", "").strip()
        if ref_game:
            embed.add_field(name="👤 Пригласил (Ник в игре)", value=ref_game)
        if ref_discord:
            embed.add_field(name="👤 Пригласил (Ник в Discord)", value=ref_discord)
        if boosts:
            items = await asyncio.to_thread(interaction.client.repo.get_all_items)
            item_map = {it["name"].lower(): it for it in items}
            boost_lines = []
            for b in boosts:
                it = item_map.get(b.lower())
                e = resolve_emoji(it.get("emoji", ""), interaction.guild) if it else ""
                emoji_str = e + " " if e else ""
                boost_lines.append(f"• {emoji_str}{b}")
            embed.add_field(name="📦 Выбранные бусты", value="\n".join(boost_lines), inline=False)

        # Screenshot from modal URL
        screenshot_url = text_data.get("screenshot_url", "").strip()
        if screenshot_url:
            embed.set_image(url=screenshot_url)

        embed.set_footer(text="Клондайк Шёпота")

        await interaction.channel.send(content=interaction.user.mention, embed=embed)

        await self._duplicate_to_audit(interaction, embed, category, text_data, delivery, boosts, screenshot_url)

        entry = {
            "type": "form", "timestamp": discord.utils.utcnow().isoformat(),
            "user_id": interaction.user.id, "user_name": str(interaction.user),
            "category": category,
            "data": {k: v for k, v in text_data.items() if v},
            "delivery_method": delivery, "selected_boosts": boosts,
        }
        await _save_deal_report(interaction.channel_id, entry)

        # Fallback: allow screenshot as attachment within 60s (only if no URL provided)
        if not screenshot_url:
            prompt = await interaction.channel.send(
                "📸 Если хотите прикрепить скриншот, отправьте изображение в этот канал в течение **60 секунд**."
            )

            def check(m):
                return (m.author == interaction.user and m.channel == interaction.channel
                        and m.attachments and m.attachments[0].content_type
                        and m.attachments[0].content_type.startswith("image/"))

            try:
                wait_msg = await interaction.client.wait_for("message", timeout=60.0, check=check)
                att = wait_msg.attachments[0]
                fp = await att.to_file()
                embed.set_image(url="attachment://screenshot.png")
                await interaction.channel.send(content=interaction.user.mention, embed=embed, file=fp)
                await self._duplicate_to_audit_with_attachment(interaction, embed, fp)
                await prompt.delete()
            except asyncio.TimeoutError:
                pass

        form_store.clear(interaction.user.id)

    async def _duplicate_to_audit(self, interaction, embed, category, text_data, delivery, boosts, screenshot_url=""):
        try:
            audit = interaction.client.audit_logger
            details = {
                "Категория": category,
                "Ник в игре": text_data.get("game_nick", ""),
            }
            if delivery:
                details["Способ"] = delivery
            ref_game = text_data.get("referrer_game", "").strip()
            ref_discord = text_data.get("referrer_discord", "").strip()
            if ref_game:
                details["Пригласил (игра)"] = ref_game
            if ref_discord:
                details["Пригласил (Discord)"] = ref_discord
            if boosts:
                details["Бусты"] = ", ".join(boosts)
            if screenshot_url:
                details["Скриншот"] = screenshot_url
            await audit.log(interaction.user, f"/ticket_form [{category}]", details)
        except Exception:
            pass

    async def _duplicate_to_audit_with_attachment(self, interaction, embed, file):
        try:
            channel = interaction.client.audit_logger._bot.get_channel(interaction.client.audit_logger._channel_id)
            if channel:
                await channel.send(embed=embed, file=file)
        except Exception:
            pass


# ------------------------------------------------------------------ #
#  View: выбор бустов (для «Заказ бустов»)                           #
# ------------------------------------------------------------------ #

class BoostSelectView(discord.ui.View):

    def __init__(self, boost_items: list[dict]):
        super().__init__(timeout=180)
        self.boost_items = boost_items
        options = []
        for it in boost_items[:25]:
            label = it["name"][:100]
            options.append(discord.SelectOption(label=label, value=it["name"]))
        if not options:
            options.append(discord.SelectOption(label="Нет доступных бустов", value=""))
        self._boost_select = discord.ui.Select(
            placeholder="Выберите бусты...",
            custom_id="boost_multi",
            options=options,
            min_values=1, max_values=min(len(options), 10),
        )
        self._boost_select.callback = self._on_boost_select
        self.add_item(self._boost_select)

        self._confirm_btn = discord.ui.Button(label="✅ Подтвердить", style=discord.ButtonStyle.success, custom_id="boost_confirm")
        self._confirm_btn.callback = self._on_confirm
        self.add_item(self._confirm_btn)

        self._add_btn = discord.ui.Button(label="➕ Добавить ещё", style=discord.ButtonStyle.secondary, custom_id="boost_add")
        self._add_btn.callback = self._on_add_more
        self.add_item(self._add_btn)

    @classmethod
    async def create(cls, interaction: discord.Interaction):
        items = await asyncio.to_thread(interaction.client.repo.get_all_items)
        boost_items = [it for it in items if it.get("category") == "boost"]
        return cls(boost_items)

    async def _on_boost_select(self, interaction: discord.Interaction):
        selected = self._boost_select.values
        current = form_store.get(interaction.user.id).get("selected_boosts", [])
        for s in selected:
            if s not in current:
                current.append(s)
        form_store.set(interaction.user.id, "selected_boosts", current)
        await interaction.response.defer(ephemeral=True)

    async def _on_confirm(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        modal_obj = TicketFormModal(form_store.get(interaction.user.id).get("category", "Заказ бустов"))
        await modal_obj._finalize(interaction)

    async def _on_add_more(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)


# ------------------------------------------------------------------ #
#  Персистентная кнопка открытия формы                               #
# ------------------------------------------------------------------ #

class TicketFormView(discord.ui.View):

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="📝 Заполнить форму", style=discord.ButtonStyle.primary, custom_id="ticket_form:open")
    async def open_form(self, interaction: discord.Interaction, _button: discord.ui.Button):
        category = self._get_category(interaction)
        if category is None:
            await interaction.response.send_message("Этот канал не является каналом тикета.", ephemeral=True)
            return
        view = DeliveryMethodView(category)
        tip = (
            "**📌 Важная информация:**\n"
            "• Ник для отправки: **Scaryyyyy**\n"
            "• Деньги и ресурсы отправляются **только после подтверждения заказа**\n"
            "• Не забудьте приложить скриншот в форме, если требуется"
        )
        await interaction.response.send_message(f"{tip}\n\n**Выберите способ:**", view=view, ephemeral=True)

    @staticmethod
    def _get_category(interaction: discord.Interaction) -> Optional[str]:
        ch_id = interaction.channel.category_id if hasattr(interaction.channel, "category_id") else interaction.channel_id
        for name, cid in CATEGORY_CHANNELS.items():
            if cid == ch_id:
                return name
        return None


# ------------------------------------------------------------------ #
#  Основной ког                                                      #
# ------------------------------------------------------------------ #

class TicketCog(commands.Cog):

    def __init__(self, bot: commands.Bot, ocr_service: OcrService, audit_logger) -> None:
        self.bot = bot
        self.ocr = ocr_service
        self.audit_logger = audit_logger
        self._view = TicketFormView()
        bot.add_view(self._view)

    async def cog_load(self) -> None:
        for category, channel_id in CATEGORY_CHANNELS.items():
            await self._ensure_form_message(channel_id, category)

    async def _ensure_form_message(self, channel_id: int, category: str) -> None:
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            logger.warning("Канал %s (ID %s) не найден", category, channel_id)
            return
        if isinstance(channel, discord.CategoryChannel):
            text_ch = next((c for c in channel.text_channels), None)
            if text_ch is None:
                logger.warning("В категории %s нет текстовых каналов", category)
                return
            channel = text_ch
        try:
            async for msg in channel.history(limit=30):
                if msg.author == self.bot.user and msg.components:
                    return
        except Exception:
            pass
        await self._send_form_to_channel(channel, category)

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

    async def _send_form_to_channel(self, channel, category: str) -> None:
        embed = discord.Embed(
            title=f"📋 {category}",
            description=(
                "Для оформления сделки нажмите кнопку ниже и заполните форму.\n"
                "Вы также можете прикрепить скриншот — бот автоматически "
                "распознает предметы и рассчитает сумму."
            ),
            colour=discord.Colour.blurple(),
        )
        try:
            await channel.send(embed=embed, view=self._view)
        except Exception as e:
            logger.warning("Не удалось отправить форму в %s: %s", channel.id, e)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        is_ticket = message.channel.id in CATEGORY_CHANNELS.values()
        is_monitored = message.channel.id in MONITORED_CHANNELS
        if not is_ticket and not is_monitored:
            return
        images = [a for a in message.attachments if a.content_type and a.content_type.startswith("image/")]
        if not images:
            return
        await message.add_reaction("⏳")
        for attachment in images:
            await self._process_image(message, attachment)
        await message.remove_reaction("⏳", self.bot.user)
        await message.add_reaction("✅")

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


# ------------------------------------------------------------------ #
#  Вспомогательные функции                                           #
# ------------------------------------------------------------------ #

async def _save_deal_report(channel_id: int, entry: dict) -> None:
    os.makedirs(DEAL_REPORTS_DIR, exist_ok=True)
    path = os.path.join(DEAL_REPORTS_DIR, f"{channel_id}.json")

    def _sync_save() -> None:
        try:
            with open(path, encoding="utf-8") as f:
                report: list[dict] = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            report = []
        report.append(entry)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

    await asyncio.to_thread(_sync_save)


async def setup(bot: commands.Bot) -> None:
    ocr_service = OcrService()
    await bot.add_cog(TicketCog(bot, ocr_service, bot.audit_logger))
