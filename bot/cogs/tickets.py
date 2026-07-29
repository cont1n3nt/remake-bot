"""Ког для автоматизации тикетов: форма, OCR-скан изображений, команда /tag."""

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

logger = logging.getLogger("bot")

# Поля модалки для каждой категории
CATEGORY_FIELDS: dict[str, list[dict]] = {
    "Продажа предметов": [
        {"label": "Название предмета", "custom_id": "item_name",  "required": True,  "style": discord.TextStyle.short},
        {"label": "Количество",        "custom_id": "quantity",   "required": True,  "style": discord.TextStyle.short},
        {"label": "Цена за единицу",   "custom_id": "unit_price", "required": True,  "style": discord.TextStyle.short},
        {"label": "Описание",          "custom_id": "description","required": False, "style": discord.TextStyle.paragraph},
    ],
    "Продажа бустов": [
        {"label": "Название буста", "custom_id": "boost_name", "required": True,  "style": discord.TextStyle.short},
        {"label": "Стоимость",      "custom_id": "cost",       "required": True,  "style": discord.TextStyle.short},
        {"label": "Описание",       "custom_id": "description","required": False, "style": discord.TextStyle.paragraph},
    ],
    "Заказ бустов": [
        {"label": "Название буста", "custom_id": "boost_name", "required": True,  "style": discord.TextStyle.short},
        {"label": "Сервис",         "custom_id": "service",    "required": True,  "style": discord.TextStyle.short},
        {"label": "Бюджет",         "custom_id": "budget",     "required": True,  "style": discord.TextStyle.short},
        {"label": "Описание",       "custom_id": "description","required": False, "style": discord.TextStyle.paragraph},
    ],
}

# Директория для хранения отчётов по сделкам
DEAL_REPORTS_DIR = "deal_reports"

# ------------------------------------------------------------------ #
#  View с кнопкой открытия формы                                     #
# ------------------------------------------------------------------ #

class TicketFormView(discord.ui.View):
    """Персистентное view с кнопкой «Заполнить форму»."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="📝 Заполнить форму",
        style=discord.ButtonStyle.primary,
        custom_id="ticket_form:open",
    )
    async def open_form(
        self, interaction: discord.Interaction, _button: discord.ui.Button,
    ) -> None:
        # Определяем категорию по ID канала
        category = self._get_category(interaction)
        if category is None:
            await interaction.response.send_message(
                "Этот канал не является каналом тикета.", ephemeral=True,
            )
            return

        modal = TicketFormModal(category)
        await interaction.response.send_modal(modal)

    @staticmethod
    def _get_category(interaction: discord.Interaction) -> Optional[str]:
        ch_id = interaction.channel.category_id if hasattr(interaction.channel, "category_id") else interaction.channel_id
        for name, cid in CATEGORY_CHANNELS.items():
            if cid == ch_id:
                return name
        return None

# ------------------------------------------------------------------ #
#  Модальное окно формы тикета                                       #
# ------------------------------------------------------------------ #

class TicketFormModal(discord.ui.Modal):
    """Модальное окно с полями, зависящими от категории тикета."""

    def __init__(self, category: str) -> None:
        super().__init__(title=f"Форма — {category}")
        self.category = category

        for field in CATEGORY_FIELDS.get(category, []):
            self.add_item(discord.ui.TextInput(
                label=field["label"],
                custom_id=field["custom_id"],
                required=field["required"],
                style=field.get("style", discord.TextStyle.short),
                placeholder=field.get("placeholder", ""),
                max_length=field.get("max_length", 4000),
            ))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        # Собираем данные из полей формы
        data: dict[str, str] = {}
        for child in self.children:
            if isinstance(child, discord.ui.TextInput):
                data[child.custom_id] = child.value

        entry: dict = {
            "type": "form",
            "timestamp": discord.utils.utcnow().isoformat(),
            "user_id": interaction.user.id,
            "user_name": str(interaction.user),
            "category": self.category,
            "data": data,
        }

        # Сохраняем в файл отчёта по каналу
        await _save_deal_report(interaction.channel_id, entry)

        await interaction.response.send_message(
            "✅ Форма успешно отправлена!",
            ephemeral=True,
        )

# ------------------------------------------------------------------ #
#  Основной ког                                                      #
# ------------------------------------------------------------------ #

class TicketCog(commands.Cog):
    """Автоматизация тикетов: кнопка формы, OCR скриншотов, /tag."""

    def __init__(self, bot: commands.Bot, ocr_service: OcrService) -> None:
        self.bot = bot
        self.ocr = ocr_service
        # Регистрируем персистентное view (обрабатывает кнопки после перезапуска)
        self._view = TicketFormView()
        bot.add_view(self._view)

    # ------------------------------------------------------------------
    #  При загрузке кога — размещаем кнопку формы в каждом канале
    # ------------------------------------------------------------------

    async def cog_load(self) -> None:
        for category, channel_id in CATEGORY_CHANNELS.items():
            await self._ensure_form_message(channel_id, category)

    async def _ensure_form_message(self, channel_id: int, category: str) -> None:
        """Проверяет, есть ли уже сообщение с формой в канале, и при
        необходимости создаёт новое.

        Если channel_id — это категория, берём первый текстовый канал внутри."""
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

    # ------------------------------------------------------------------
    #  Отправка формы при создании нового тикет-канала
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        """При создании нового канала в категории тикетов отправляет форму."""
        if not isinstance(channel, discord.TextChannel):
            return
        if channel.category_id not in CATEGORY_CHANNELS.values():
            return
        category_name = next((n for n, cid in CATEGORY_CHANNELS.items() if cid == channel.category_id), None)
        if category_name is None:
            return
        await self._send_form_to_channel(channel, category_name)

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread) -> None:
        """При создании нового треда отправляет форму."""
        parent = thread.parent
        if parent is None:
            return
        if isinstance(parent, discord.ForumChannel):
            category_name = next((n for n, cid in CATEGORY_CHANNELS.items() if cid == parent.id), None)
            if category_name:
                await self._send_form_to_channel(thread, category_name)
            return
        if parent.category_id is None:
            return
        if parent.category_id not in CATEGORY_CHANNELS.values():
            return
        category_name = next((n for n, cid in CATEGORY_CHANNELS.items() if cid == parent.category_id), None)
        if category_name is None:
            return
        await self._send_form_to_channel(thread, category_name)

    async def _send_form_to_channel(self, channel, category: str) -> None:
        """Отправляет приветственное сообщение с формой в указанный канал."""
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

    # ------------------------------------------------------------------
    #  OCR на вложенных изображениях
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Обрабатывает изображения, прикреплённые к сообщениям
        в тикет-каналах и отслеживаемых каналах."""
        if message.author.bot:
            return

        is_ticket = message.channel.id in CATEGORY_CHANNELS.values()
        is_monitored = message.channel.id in MONITORED_CHANNELS
        if not is_ticket and not is_monitored:
            return

        images = [
            a for a in message.attachments
            if a.content_type and a.content_type.startswith("image/")
        ]
        if not images:
            return

        await message.add_reaction("⏳")

        for attachment in images:
            await self._process_image(message, attachment)

        await message.remove_reaction("⏳", self.bot.user)
        await message.add_reaction("✅")

    async def _process_image(
        self, message: discord.Message, attachment: discord.Attachment,
    ) -> None:
        """Скачивает изображение, запускает OCR, сохраняет результат."""
        try:
            await self.ocr.reload_prices()
            img_bytes = await attachment.read()

            # OCR с таймаутом 30 секунд
            text = await asyncio.wait_for(
                self.ocr.extract_text(img_bytes),
                timeout=30.0,
            )
            text = text.strip()
            if not text:
                logger.info("OCR не нашёл текст на изображении %s", attachment.filename)
                return

            # Парсим предметы и сверяем с ценами
            parsed = self.ocr.parse_items(text)
            readable, total, unknown = self.ocr.cross_reference(parsed)

            # Формируем строку результата
            items_str = "; ".join(readable)
            result = f"[OCR Result] Items: {items_str} | Total: {_fmt(total)} RUB"

            # Сохраняем в отчёт
            entry: dict = {
                "type": "ocr",
                "timestamp": discord.utils.utcnow().isoformat(),
                "user_id": message.author.id,
                "user_name": str(message.author),
                "message_id": message.id,
                "filename": attachment.filename,
                "items": parsed,
                "total": total,
                "unknown": unknown,
                "raw_text": text,
            }
            await _save_deal_report(message.channel.id, entry)

            # Печатаем результат в канал
            await message.channel.send(f"```{result}```")

            # Если есть неизвестные предметы — предупреждаем
            if unknown:
                await message.channel.send(
                    f"⚠️ Не удалось определить цену для: {', '.join(unknown)}. "
                    "Проверьте базу цен или укажите стоимость вручную.",
                )

        except asyncio.TimeoutError:
            await message.channel.send("⏱ OCR-распознавание превысило таймаут (30 с).")
        except ValueError as exc:
            await message.channel.send(f"⚠️ {exc}")
        except Exception:
            logger.exception("Ошибка OCR при обработке %s", attachment.filename)
            await message.channel.send("⚠️ Произошла ошибка при OCR-распознавании.")

    # ------------------------------------------------------------------
    #  Команда /tag — уведомление участника в ЛС
    # ------------------------------------------------------------------

    @app_commands.command(name="tag", description="💬 (Админ) Отправить уведомление пользователю в личные сообщения о тикете")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(user="Уведомить участника о тикете в личных сообщениях")
    async def tag(
        self, interaction: discord.Interaction, user: discord.User,
    ) -> None:
        """Отправляет пользователю Embed-уведомление в ЛС со ссылкой на канал."""
        await interaction.response.defer(ephemeral=True)

        # Embed-карточка уведомления
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
        embed.add_field(
            name="🔮 От Главы Шёпота",
            value="Команда «Клондайк Шёпота»",
            inline=False,
        )
        # Аватарка сервера в footer
        icon = interaction.guild.icon.url if interaction.guild and interaction.guild.icon else None
        embed.set_footer(
            text="Маркетплейс «Клондайк Шёпота»",
            icon_url=icon,
        )
        embed.timestamp = discord.utils.utcnow()

        # Кнопка-ссылка на канал
        view = discord.ui.View()
        view.add_item(discord.ui.Button(
            label="🔗 Перейти к тикету",
            url=interaction.channel.jump_url,
            style=discord.ButtonStyle.link,
        ))

        try:
            await user.send(embed=embed, view=view)
            await interaction.followup.send(
                f"✅ Уведомление отправлено пользователю {user.mention}.",
                ephemeral=True,
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "Не удалось отправить сообщение пользователю.",
                ephemeral=True,
            )

    @tag.error
    async def tag_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError,
    ) -> None:
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
    """Асинхронное сохранение записи в JSON-отчёт канала."""
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
    await bot.add_cog(TicketCog(bot, ocr_service))
