"""Шаг 1 визарда тикета: выбор способа получения, стартовые модалки формы,
и персистентная кнопка «Заполнить форму».

Перенесено из bot/cogs/tickets.py без изменений (REFACTORING_PLAN.md,
Этап F.4a).

⚠ Оставшийся отложенный (локальный) импорт внутри `BaseOrderModal._publish`
(`EditRequestView` из views_edit.py, пока эта заявка не вынесена — см.
Этап F.4d) — не забытый верхнеуровневый импорт, а часть разрыва
циклической зависимости views_delivery.py ↔ views_boosts.py ↔
views_edit.py: `BoostQuantityView` в views_boosts.py, в свою очередь,
нужен `BoostOrderModal` отсюда и `EditRequestView` из views_edit.py — эти
два обратных ребра остаются отложенными навсегда (см. REFACTOR_PROGRESS.md,
Фаза F, F.4a, для полной схемы зависимостей). `BoostSelectionView` уже
можно импортировать в шапке — `views_boosts.py` не импортирует этот файл
на уровне модуля."""

from typing import Optional

import discord

from bot.config.constants import CATEGORY_CHANNELS
from bot.cogs.tickets.embeds import _build_request_card_embed
from bot.cogs.tickets.views_boosts import BoostSelectionView
from bot.cogs.tickets.storage import form_store, _save_request_meta, _save_deal_report


# ------------------------------------------------------------------ #
#  Шаг 1: Выбор способа получения (Почта / Трейд)                    #
# ------------------------------------------------------------------ #

class DeliveryMethodSelect(discord.ui.Select):

    def __init__(self, category: str):
        options = [
            discord.SelectOption(label="Почта", emoji="📮", value="Почта"),
            discord.SelectOption(label="Трейд", emoji="🤝", value="Трейд"),
        ]
        super().__init__(placeholder="Выберите способ получения", options=options, custom_id="delivery_method")
        self.category = category

    async def callback(self, interaction: discord.Interaction):
        form_store.set(interaction.user.id, "delivery_method", self.values[0])
        if "Заказ" in self.category:
            modal = BoostOrderModal(self.category)
        else:
            modal = SaleModal(self.category)
        await interaction.response.send_modal(modal)


class DeliveryMethodView(discord.ui.View):
    def __init__(self, category: str):
        super().__init__(timeout=120)
        self.add_item(DeliveryMethodSelect(category))


# ------------------------------------------------------------------ #
#  Шаг 2: Modal (разный для заказа бустов и продажи)                #
# ------------------------------------------------------------------ #

class BaseOrderModal(discord.ui.Modal):

    def __init__(self, category: str, title_text: str):
        super().__init__(title=title_text, timeout=300)
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
        from bot.cogs.tickets import EditRequestView, ScreenshotPromptView

        store = form_store.get(interaction.user.id)
        text_data = store.get("text_data", {})
        delivery = store.get("delivery_method", "")
        boosts = store.get("selected_boosts", [])
        total_price = store.get("total_price", 0.0)

        embed = _build_request_card_embed(interaction, text_data, delivery, boosts, total_price, store.get("category", ""))

        msg = await interaction.channel.send(embed=embed, view=EditRequestView())
        message_id = msg.id

        request_data = {
            "text_data": text_data,
            "delivery_method": delivery,
            "selected_boosts": boosts,
            "total_price": total_price,
            "category": store.get("category", ""),
        }
        await _save_request_meta(interaction.channel_id, message_id, interaction.user.id, request_data)

        entry = {
            "type": "form",
            "timestamp": discord.utils.utcnow().isoformat(),
            "user_id": interaction.user.id,
            "user_name": str(interaction.user),
            "category": store.get("category", ""),
            "data": {k: v for k, v in text_data.items() if v},
            "delivery_method": delivery,
            "selected_boosts": boosts,
            "message_id": message_id,
        }
        await _save_deal_report(interaction.channel_id, entry)

        try:
            audit = interaction.client.audit_logger
            audit_details = {
                "Категория": store.get("category", ""),
                "Ник в игре": text_data.get("game_nick", ""),
            }
            if delivery:
                audit_details["Способ"] = delivery
            ref_game = text_data.get("referrer_game", "").strip()
            ref_discord = text_data.get("referrer_discord", "").strip()
            if ref_game:
                audit_details["Пригласил (игра)"] = ref_game
            if ref_discord:
                audit_details["Пригласил (Discord)"] = ref_discord
            if boosts:
                boost_names = [b["name"] for b in boosts]
                audit_details["Бусты"] = ", ".join(boost_names)
            await audit.log(interaction.user, f"/ticket_form [{store.get('category', '')}]", audit_details)
        except Exception:
            pass

        screenshot_view = ScreenshotPromptView(interaction.user.id, msg, embed)
        await interaction.followup.send(
            "📷 **Прикрепите изображение следующим сообщением.**",
            view=screenshot_view,
            ephemeral=True,
        )

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
                    "• Деньги отправляются **только после подтверждения сделки**\n"
                    "• Приложите скриншот для подтверждения\n\n"
                    "**Выберите способ получения:**"
                ),
                colour=discord.Colour.blurple(),
            )

        view = DeliveryMethodView(category)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @staticmethod
    def _get_category(interaction: discord.Interaction) -> Optional[str]:
        ch_id = interaction.channel.category_id if hasattr(interaction.channel, "category_id") else interaction.channel_id
        for name, cid in CATEGORY_CHANNELS.items():
            if cid == ch_id:
                return name
        return None
