"""Редактирование и подтверждение опубликованной заявки.

Перенесено из bot/cogs/tickets.py без изменений (REFACTORING_PLAN.md,
Этап F.4d — последний под-этап Фазы F.4)."""

import logging

import discord

from bot.cogs.tickets.embeds import _build_request_card_embed
from bot.cogs.tickets.views_boosts import BoostSelectionView
from bot.cogs.tickets.storage import form_store, _save_request_meta, _load_request_meta

logger = logging.getLogger("bot")


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
