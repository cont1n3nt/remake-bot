"""Шаг 1 визарда тикета: выбор способа получения, стартовые модалки формы,
и персистентная кнопка «Заполнить форму».

Перенесено из bot/cogs/tickets.py без изменений (REFACTORING_PLAN.md,
Этап F.4a).

⚠ Все связи с соседними подмодулями пакета tickets теперь в шапке файла.
Единственное обратное ребро цикла views_delivery.py ↔ views_boosts.py ↔
views_edit.py — `BoostQuantityView._on_confirm` в views_boosts.py,
которому нужны `BoostOrderModal` (отсюда) и `EditRequestView`
(views_edit.py) — остаётся отложенным (локальным) импортом внутри того
метода, навсегда (см. REFACTOR_PROGRESS.md, Фаза F, F.4a/F.4d, для полной
схемы зависимостей)."""

import asyncio
import logging
from typing import Optional

import discord

from bot.config.constants import CATEGORY_CHANNELS
from bot.cogs.tickets.embeds import _build_request_card_embed, build_audit_details
from bot.cogs.tickets.views_boosts import BoostSelectionView
from bot.cogs.tickets.views_screenshot import ScreenshotPromptView
from bot.cogs.tickets.views_edit import EditRequestView
from bot.cogs.tickets.storage import (
    form_store, _save_request_meta, _save_deal_report, _load_request_meta_by_channel,
)

logger = logging.getLogger("bot")

# Идёт ли прямо сейчас публикация карточки для (канал, пользователь) — защита
# от дублей при повторном сабмите модалки и двойном клике (пункт 8).
_publishing: set[tuple[int, int]] = set()

# Требования к скриншоту: без них OCR не может сопоставить предметы с базой цен
# и посчитать сумму заявки (пункт 11).
SCREENSHOT_REQUIREMENTS = (
    "📷 **Прикрепите скриншот следующим сообщением.**\n\n"
    "**Чтобы бот посчитал сумму автоматически:**\n"
    "• снимок целиком, без обрезки — видны все предметы и их количество\n"
    "• подписи количества (например `227x`) и названия читаемы, не размыты\n"
    "• отправляйте файл изображением, а не ссылкой и не документом\n"
    "• один скриншот на сообщение, без наложений и стикеров поверх\n\n"
    "Если распознать не удастся, сумму посчитает администратор вручную."
)


# ------------------------------------------------------------------ #
#  Шаг 1: Выбор способа получения (Почта / Трейд)                    #
# ------------------------------------------------------------------ #

class DeliveryMethodSelect(discord.ui.Select):

    def __init__(self):
        options = [
            discord.SelectOption(label="Почта", emoji="📮", value="Почта"),
            discord.SelectOption(label="Трейд", emoji="🤝", value="Трейд"),
        ]
        super().__init__(placeholder="Выберите способ получения", options=options, custom_id="delivery_method")

    async def callback(self, interaction: discord.Interaction):
        # Категорию берём из канала, а не из состояния объекта: представление
        # персистентное и переживает перезапуск бота (пункт 1).
        category = TicketFormView._get_category(interaction)
        if category is None:
            await interaction.response.send_message(
                "Этот канал не является каналом тикета.", ephemeral=True,
            )
            return
        form_store.set(interaction.user.id, "delivery_method", self.values[0])
        modal = BoostOrderModal(category) if "Заказ" in category else SaleModal(category)
        await interaction.response.send_modal(modal)


class DeliveryMethodView(discord.ui.View):
    """Персистентное представление.

    Раньше здесь стоял timeout=120: после таймаута (или после перезапуска бота)
    callback селекта уже не был зарегистрирован, и Discord отвечал
    «Приложение Связной не ответило вовремя» (пункт 1).
    """

    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(DeliveryMethodSelect())


# ------------------------------------------------------------------ #
#  Шаг 2: Modal (разный для заказа бустов и продажи)                #
# ------------------------------------------------------------------ #

class BaseOrderModal(discord.ui.Modal):

    # Параметр называется именно `title`: наследники зовут super().__init__(
    # category, title=...), и при имени `title_text` конструктор падал с
    # TypeError — форма тикета не открывалась вовсе.
    def __init__(self, category: str, title: str):
        super().__init__(title=title, timeout=300)
        self.category = category

        self.add_item(discord.ui.TextInput(
            label="Игровой ник",
            custom_id="game_nick",
            required=True,
            style=discord.TextStyle.short,
            placeholder="Введите ваш игровой никнейм",
        ))

        if "Заказ" in self.category:
            self.add_item(discord.ui.TextInput(
                label="До какой даты выполнить",
                custom_id="deadline",
                required=True,
                style=discord.TextStyle.short,
                placeholder="ДД.ММ.ГГГГ ЧЧ:ММ",
            ))

        self.add_item(discord.ui.TextInput(
            label="Кто пригласил (игра)",
            custom_id="referrer_game",
            required=False,
            style=discord.TextStyle.short,
        ))

        self.add_item(discord.ui.TextInput(
            label="Кто пригласил (Discord)",
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

        selected = form_store.get(interaction.user.id).get("selected_boosts", [])
        if "Заказ" in self.category:
            view = await BoostSelectionView.create(interaction, selected)
            content = "**Выберите нужные бусты:**"
            await interaction.followup.send(content, view=view, ephemeral=True)
        else:
            boosts = []
            total_price = 0.0
            form_store.set(interaction.user.id, "selected_boosts", boosts)
            form_store.set(interaction.user.id, "total_price", total_price)
            await self._publish(interaction)

    async def _publish(self, interaction: discord.Interaction):
        # Один пользователь — одна публикация за раз. Повторный сабмит модалки и
        # двойной клик по «Подтвердить» раньше давали две одинаковые карточки
        # в канале (пункт 8).
        lock_key = (interaction.channel_id, interaction.user.id)
        if lock_key in _publishing:
            return
        _publishing.add(lock_key)
        try:
            await self._publish_locked(interaction)
        finally:
            _publishing.discard(lock_key)

    async def _already_published(self, interaction: discord.Interaction) -> bool:
        """Есть ли в этом канале живая карточка того же пользователя.

        Замок `_publishing` спасает только от одновременных вызовов, а дубли
        возникали и от последовательных: повторный сабмит модалки, повторная
        доставка взаимодействия, второй клик по «Подтвердить» (пункт 8).
        Один тикет — одна заявка, поэтому вторую не публикуем."""
        meta = await asyncio.to_thread(_load_request_meta_by_channel, interaction.channel_id)
        if meta is None:
            return False
        message_id, meta_data = meta
        if meta_data.get("user_id") != interaction.user.id:
            return False
        try:
            await interaction.channel.fetch_message(message_id)
        except (discord.NotFound, discord.HTTPException):
            return False  # карточку удалили — можно публиковать заново
        return True

    async def _publish_locked(self, interaction: discord.Interaction):
        if await self._already_published(interaction):
            logger.info(
                "Заявка в канале %s для пользователя %s уже опубликована — дубль отменён",
                interaction.channel_id, interaction.user.id,
            )
            return

        store = form_store.get(interaction.user.id)
        text_data = store.get("text_data", {})
        delivery = store.get("delivery_method", "")
        boosts = store.get("selected_boosts", [])
        total_price = store.get("total_price", 0.0)
        category = store.get("category", "")

        embed = _build_request_card_embed(interaction, text_data, delivery, boosts, total_price, category)

        # allowed_mentions=none: упоминание создателя внутри карточки не должно
        # порождать отдельный пинг над эмбедом (пункт 6).
        msg = await interaction.channel.send(
            embed=embed,
            view=EditRequestView(),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        message_id = msg.id

        request_data = {
            "text_data": text_data,
            "delivery_method": delivery,
            "selected_boosts": boosts,
            "total_price": total_price,
            "category": category,
        }

        # Лог заявки отправляем один раз и запоминаем его id: при последующих
        # правках заявки тот же лог редактируется, а не дублируется (пункт 14).
        try:
            audit = interaction.client.audit_logger
            details = build_audit_details(
                interaction.client.repo, interaction.guild,
                text_data, delivery, boosts, total_price, category,
            )
            log_msg = await audit.log(interaction.user, "/ticket_form", details)
            if log_msg is not None:
                request_data["log_message_id"] = log_msg.id
        except Exception:
            logger.exception("Не удалось записать лог заявки в канале %s", interaction.channel_id)

        await _save_request_meta(interaction.channel_id, message_id, interaction.user.id, request_data)

        entry = {
            "type": "form",
            "timestamp": discord.utils.utcnow().isoformat(),
            "user_id": interaction.user.id,
            "user_name": str(interaction.user),
            "category": category,
            "data": {k: v for k, v in text_data.items() if v},
            "delivery_method": delivery,
            "selected_boosts": boosts,
            "message_id": message_id,
        }
        await _save_deal_report(interaction.channel_id, entry)

        # Скриншот нужен только при продаже: в заказе бустов состав и так задан
        # формой, поэтому ни запроса скриншота, ни OCR там больше нет (пункт 7).
        if "Заказ" not in category:
            await interaction.followup.send(
                SCREENSHOT_REQUIREMENTS,
                view=ScreenshotPromptView(interaction.user.id),
                ephemeral=True,
            )
        else:
            await interaction.followup.send("✅ Заявка опубликована.", ephemeral=True)

        form_store.clear(interaction.user.id)

class BoostOrderModal(BaseOrderModal):

    def __init__(self, category: str):
        super().__init__(category, title="Форма — Заказ бустов")


class SaleModal(BaseOrderModal):

    def __init__(self, category: str):
        title_text = "Форма — Продажа" if "Продажа" in category else "Форма — Заказ"
        super().__init__(category, title=title_text)


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

        if "Заказ" in category:
            embed = discord.Embed(
                title="📋 Оформление заказа бустов",
                description=(
                    "**Для заказа бустов:**\n"
                    "• После оформления заявки с вами свяжется администратор\n"
                    "• Бусты выполняются в порядке очереди\n\n"
                    "**Выберите способ получения:**"
                ),
                colour=discord.Colour.blurple(),
            )
        else:
            embed = discord.Embed(
                title="📋 Оформление продажи",
                description=(
                    "**Для продажи:**\n"
                    "• Деньги отправляются **только после подтверждения сделки**\n\n"
                    "**Выберите способ получения:**"
                ),
                colour=discord.Colour.blurple(),
            )

        await interaction.response.send_message(embed=embed, view=DeliveryMethodView(), ephemeral=True)

    @staticmethod
    def _get_category(interaction: discord.Interaction) -> Optional[str]:
        ch_id = interaction.channel.category_id if hasattr(interaction.channel, "category_id") else interaction.channel_id
        for name, cid in CATEGORY_CHANNELS.items():
            if cid == ch_id:
                return name
        return None
