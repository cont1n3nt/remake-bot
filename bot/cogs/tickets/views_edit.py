"""Редактирование и подтверждение опубликованной заявки.

Перенесено из bot/cogs/tickets.py без изменений (REFACTORING_PLAN.md,
Этап F.4d — последний под-этап Фазы F.4)."""

import asyncio
import logging
import re
from typing import Optional

import discord

from bot.cogs.tickets.embeds import _build_request_card_embed, build_audit_details
from bot.cogs.tickets.views_boosts import BoostSelectionView
from bot.cogs.tickets.storage import form_store, _save_request_meta, _load_request_meta

logger = logging.getLogger("bot")

_MENTION_RE = re.compile(r"^<@!?(\d{15,25})>$")
_RAW_ID_RE = re.compile(r"^(\d{15,25})$")


async def update_request_log(interaction: discord.Interaction, request_data: dict) -> None:
    """Перестроить лог заявки на месте.

    В канал логов должна попадать только итоговая форма: раньше публикация и
    каждое редактирование слали отдельные записи, и ранние версии копились
    подряд (пункт 14)."""
    log_message_id = request_data.get("log_message_id")
    if not log_message_id:
        return
    try:
        audit = interaction.client.audit_logger
        details = build_audit_details(
            interaction.client.repo, interaction.guild,
            request_data.get("text_data", {}), request_data.get("delivery_method", ""),
            request_data.get("selected_boosts", []), request_data.get("total_price", 0.0),
            request_data.get("category", ""),
        )
        await audit.edit_log(log_message_id, interaction.user, "/ticket_form", details)
    except Exception:
        logger.exception("Не удалось обновить лог заявки %s", log_message_id)


def _extract_discord_id(raw: str) -> Optional[str]:
    """Достать Discord ID из «<@123…>» или из голых цифр.

    Имя пользователя («scary») ID не является — такие значения отвергаем, чтобы
    в колонку I не попал мусор."""
    s = raw.strip()
    for pattern in (_MENTION_RE, _RAW_ID_RE):
        m = pattern.match(s)
        if m:
            return m.group(1)
    return None


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
            kwargs = {
                "embed": embed,
                "view": EditRequestView(),
                "allowed_mentions": discord.AllowedMentions.none(),
            }
            if files:
                kwargs["attachments"] = files[:1]
            await msg.edit(**kwargs)
        except (discord.NotFound, discord.HTTPException) as e:
            await interaction.followup.send("⚠️ Не удалось найти заявку для редактирования.", ephemeral=True)
            logger.warning("Edit failed: message %s not found: %s", self.message_id, e)
            return

        await interaction.followup.send("✅ Заявка обновлена.", ephemeral=True)
        await update_request_log(interaction, self.request_data)


class EditRequestView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="✏️ Изменить заказ", style=discord.ButtonStyle.secondary, custom_id="edit_request")
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
        is_admin = getattr(interaction.user.guild_permissions, "administrator", False)
        if meta.get("user_id") != interaction.user.id and not is_admin:
            await interaction.response.send_message("Это не ваша заявка.", ephemeral=True)
            return
        await interaction.response.send_modal(EditRequestModal(message_id, meta["data"]))

    @discord.ui.button(label="✅ Завершить сделку", style=discord.ButtonStyle.success, custom_id="confirm_request")
    async def confirm_callback(self, interaction: discord.Interaction, _button: discord.ui.Button):
        # Фиксация сделки — только для админов (пункт 13).
        if not getattr(interaction.user.guild_permissions, "administrator", False):
            await interaction.response.send_message(
                "Завершить сделку может только администратор.", ephemeral=True,
            )
            return
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
        await interaction.response.send_modal(ConfirmModal(message_id, meta["data"]))


# ------------------------------------------------------------------ #
#  Modal подтверждения заявки (для администратора)                   #
# ------------------------------------------------------------------ #

class ConfirmModal(discord.ui.Modal):
    """Единственное поле — сумма (пункт 13).

    Ник игрока, реферер в игре и Discord ID пригласившего берутся из самой
    заявки: администратору нечего перепечатывать вручную."""

    def __init__(self, message_id: int, request_data: dict):
        super().__init__(title="✅ Завершение сделки", timeout=120)
        self.message_id = message_id
        self.request_data = request_data

        total = request_data.get("total_price") or 0
        self.add_item(discord.ui.TextInput(
            label="Сумма сделки",
            custom_id="amount",
            required=True,
            style=discord.TextStyle.short,
            placeholder="Поддерживает выражения: 727 540 + 110 222",
            default=str(int(total)) if total else "",
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
        # Реферер в игре уезжает в колонку H «Пришел от» внутри append_transaction.
        await transactions_cog.record_transaction(interaction, tx_type, nick, amount, referrer_game or None)

        # Discord пригласившего — в колонку I, привязка по уникальному нику (пункт 13).
        # Оба поля «Кто пригласил» описывают одного человека, поэтому Discord ID
        # ложится на строку пригласившего — так же, как его игровой ник уходит
        # в «Пришел от».
        await self._save_referrer_discord_id(interaction, referrer_game)

    async def _save_referrer_discord_id(self, interaction: discord.Interaction, nick: str) -> None:
        raw = self.request_data.get("text_data", {}).get("referrer_discord", "").strip()
        if not raw or not nick:
            return
        discord_id = _extract_discord_id(raw)
        if discord_id is None:
            await interaction.followup.send(
                f"⚠️ «{raw}» не похоже на Discord ID или упоминание — "
                "колонка Discord ID не заполнена.",
                ephemeral=True,
            )
            return
        try:
            saved = await asyncio.to_thread(
                interaction.client.sheets_service.set_discord_id, nick, discord_id,
            )
        except Exception as e:
            logger.warning("Не удалось записать Discord ID для %s: %s", nick, e)
            return
        if not saved:
            await interaction.followup.send(
                f"⚠️ Ник «{nick}» не найден в базе пользователей — Discord ID не записан.",
                ephemeral=True,
            )
