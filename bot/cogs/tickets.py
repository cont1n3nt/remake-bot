"""Ког для автоматизации тикетов: пошаговый Wizard UI, скриншоты через attachment, редактирование заявок."""

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
RANK_ROLES_MAP = {
    "Standard": 1518324856549277827,
    "Premium": 1518328036137631805,
    "Prestige": 1518328037631066232,
    "Elite": 1518328222939611166,
    "Legend": 1518328324605083698,
}
REFERRAL_ROLES_MAP = {
    "Скаут": 1518583879672270878,
    "Промоутер": 1518584176054636584,
    "Вербовщик": 1518584268933300274,
    "Амбассадор": 1518584424818671687,
    "Рекламный Барон": 1518584494410563625,
}


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
#  Хранилище опубликованных заявок (для редактирования)              #
# ------------------------------------------------------------------ #

REQUESTS_FILE = "published_requests.json"


def _save_request_meta_sync(channel_id: int, message_id: int, user_id: int, data: dict) -> None:
    try:
        with open(REQUESTS_FILE, encoding="utf-8") as f:
            meta = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        meta = {}
    meta[str(message_id)] = {
        "channel_id": channel_id,
        "user_id": user_id,
        "data": data,
    }
    with open(REQUESTS_FILE, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


async def _save_request_meta(channel_id: int, message_id: int, user_id: int, data: dict) -> None:
    await asyncio.to_thread(_save_request_meta_sync, channel_id, message_id, user_id, data)


def _load_request_meta(message_id: int) -> Optional[dict]:
    try:
        with open(REQUESTS_FILE, encoding="utf-8") as f:
            meta = json.load(f)
        return meta.get(str(message_id))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _load_request_meta_by_channel(channel_id: int) -> Optional[tuple[int, dict]]:
    """Find the published request card for a ticket channel (most recently saved one)."""
    try:
        with open(REQUESTS_FILE, encoding="utf-8") as f:
            meta = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    match_id, match_data = None, None
    for message_id_str, data in meta.items():
        if data.get("channel_id") == channel_id:
            match_id, match_data = message_id_str, data
    if match_id is None:
        return None
    return int(match_id), match_data


async def _delete_request_meta(message_id: int) -> None:
    def _sync():
        try:
            with open(REQUESTS_FILE, encoding="utf-8") as f:
                meta = json.load(f)
            meta.pop(str(message_id), None)
            with open(REQUESTS_FILE, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    await asyncio.to_thread(_sync)


# ------------------------------------------------------------------ #
#  Единая точка построения карточки заявки (продажа / заказ бустов)  #
#  Используется при первой публикации, после изменения количества    #
#  бустов и после редактирования заявки — чтобы оформление никогда   #
#  не расходилось между тремя местами вызова.                        #
# ------------------------------------------------------------------ #

def _build_request_card_embed(
    interaction: discord.Interaction,
    text_data: dict,
    delivery: str,
    boosts: list[dict],
    total_price: float,
    category: str,
) -> discord.Embed:
    embed = discord.Embed(
        title=f"📋 Новая заявка — {category}",
        colour=discord.Colour.green(),
        timestamp=discord.utils.utcnow(),
    )
    embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)

    nick = text_data.get("game_nick", "").strip()
    if nick:
        embed.add_field(name="Ник в игре", value=nick)

    if delivery:
        embed.add_field(name="Способ", value=f"📮 {delivery}" if delivery == "Почта" else f"🤝 {delivery}")

    deadline = text_data.get("deadline", "").strip()
    if deadline:
        embed.add_field(name="До даты и времени", value=deadline)

    ref_game = text_data.get("referrer_game", "").strip()
    if ref_game:
        embed.add_field(name="Кто пригласил (игра)", value=ref_game)

    ref_discord = text_data.get("referrer_discord", "").strip()
    if ref_discord:
        embed.add_field(name="Кто пригласил (Discord)", value=ref_discord)

    if boosts:
        items_map = {}
        try:
            all_items = interaction.client.repo.get_all_items()
            for it in all_items:
                if it.get("category") == "boost":
                    items_map[it["name"].lower()] = it
        except Exception:
            pass
        boost_lines = []
        for b in boosts:
            it = items_map.get(b["name"].lower())
            e = resolve_emoji(it.get("emoji", ""), interaction.guild) if it else ""
            emoji_str = e + " " if e else ""
            qty = b.get("quantity", 1)
            boost_lines.append(f"{emoji_str}{b['name']} | x{qty}")
        embed.add_field(name="Заказанные бусты", value="\n".join(boost_lines), inline=False)
        embed.add_field(name="Общая стоимость", value=f"{_fmt(total_price)} ₽", inline=False)

    # Отдельный блок для почты — без скобок, отдельно от способа получения
    if delivery == "Почта":
        sep = "━" * 24
        if "Заказ" in category:
            mail_text = f"{sep}\n📮 Почта\nНик: {nick}\nОтправляйте деньги сразу на указанную почту.\n{sep}"
        else:
            mail_text = f"{sep}\n📮 Почта\nНик: {nick}\nОтправляйте ресурсы сразу на указанную почту.\n{sep}"
        if embed.description:
            embed.description += f"\n\n{mail_text}"
        else:
            embed.description = mail_text

    embed.set_footer(text="Клондайк Шёпота")
    return embed


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
#  Шаг 3: Multi Select для выбора бустов                              #
# ------------------------------------------------------------------ #

class BoostSelectionView(discord.ui.View):

    def __init__(self, boost_items: list[dict], selected: list[str]):
        super().__init__(timeout=180)
        self.boost_items = boost_items
        self._selected = selected

        options = []
        for it in boost_items[:25]:
            label = it["name"][:100]
            is_default = it["name"] in selected
            options.append(discord.SelectOption(
                label=label, value=it["name"], default=is_default,
            ))
        if not options:
            options.append(discord.SelectOption(label="Нет доступных бустов", value="", default=False))

        self._boost_select = discord.ui.Select(
            placeholder="Выберите бусты...",
            custom_id="boost_multi_select",
            options=options,
            min_values=0,
            max_values=min(len(options), 25),
        )
        self._boost_select.callback = self._on_select
        self.add_item(self._boost_select)

        self._add_btn = discord.ui.Button(label="➕ Добавить", style=discord.ButtonStyle.success, custom_id="boost_add")
        self._add_btn.callback = self._on_add
        self.add_item(self._add_btn)

        self._clear_btn = discord.ui.Button(label="🗑 Очистить", style=discord.ButtonStyle.danger, custom_id="boost_clear")
        self._clear_btn.callback = self._on_clear
        self.add_item(self._clear_btn)

        self._next_btn = discord.ui.Button(label="✅ Далее", style=discord.ButtonStyle.primary, custom_id="boost_next")
        self._next_btn.callback = self._on_next
        self.add_item(self._next_btn)

    @classmethod
    async def create(cls, interaction: discord.Interaction, selected: list[str] = None):
        items = await asyncio.to_thread(interaction.client.repo.get_all_items)
        boost_items = [it for it in items if it.get("category") == "boost"]
        return cls(boost_items, selected or [])

    def _get_selected_names(self) -> list[str]:
        return [it["name"] for it in self.boost_items if it["name"] in self._selected]

    async def _update_message(self, interaction: discord.Interaction):
        selected_names = self._get_selected_names()
        if selected_names:
            lines = "\n".join(f"• {n}" for n in selected_names)
            content = f"**Выбранные бусты:**\n{lines}\n\n**Добавьте ещё или нажмите ✅ Далее**"
        else:
            content = "**Выберите нужные бусты:**"
        await interaction.response.edit_message(content=content, view=self)

    async def _on_select(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

    async def _on_add(self, interaction: discord.Interaction):
        selected_in_menu = self._boost_select.values
        for s in selected_in_menu:
            if s not in self._selected:
                self._selected.append(s)
        form_store.set(interaction.user.id, "selected_boosts", [{"name": n, "quantity": 1} for n in self._selected])
        await self._update_message(interaction)

    async def _on_clear(self, interaction: discord.Interaction):
        self._selected = []
        form_store.set(interaction.user.id, "selected_boosts", [])
        await self._update_message(interaction)

    async def _on_next(self, interaction: discord.Interaction):
        if not self._selected:
            await interaction.response.send_message("Выберите хотя бы один буст.", ephemeral=True)
            return
        edit_request_data = form_store.get(interaction.user.id).get("edit_request_data")
        existing_quantities = {}
        if edit_request_data:
            for b in edit_request_data.get("selected_boosts", []):
                existing_quantities[b["name"].lower()] = b.get("quantity", 1)
        boosts = []
        for n in self._selected:
            qty = existing_quantities.get(n.lower(), 1)
            boosts.append({"name": n, "quantity": qty})
        form_store.set(interaction.user.id, "selected_boosts", boosts)
        view = await BoostQuantityView.create(interaction)
        await interaction.response.edit_message(content="**Настройте количество каждого буста:**", view=view)


# ------------------------------------------------------------------ #
#  Шаг 4: Количество бустов (➕/➖/✏️ Изменить)                      #
# ------------------------------------------------------------------ #

class QuantityEditModal(discord.ui.Modal):

    def __init__(self, boost_index: int, current_qty: int, boost_name: str):
        super().__init__(title=f"Изменить количество — {boost_name}", timeout=120)
        self.boost_index = boost_index
        self.add_item(discord.ui.TextInput(
            label="Количество",
            custom_id="new_qty",
            required=True,
            style=discord.TextStyle.short,
            placeholder="Введите любое положительное число",
            default=str(current_qty),
        ))

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.children[0].value if self.children else ""
        try:
            qty = int(raw.strip())
            if qty < 1:
                raise ValueError
        except (ValueError, AttributeError):
            await interaction.response.send_message("Введите положительное целое число.", ephemeral=True)
            return
        boosts = form_store.get(interaction.user.id).get("selected_boosts", [])
        if 0 <= self.boost_index < len(boosts):
            boosts[self.boost_index]["quantity"] = qty
            form_store.set(interaction.user.id, "selected_boosts", boosts)
        view = await BoostQuantityView.create(interaction)
        await interaction.response.edit_message(content="**Настройте количество каждого буста:**", embed=None, view=view)


class BoostQuantityView(discord.ui.View):

    def __init__(self, boosts_with_qty: list[dict], page: int = 0, per_page: int = 4):
        super().__init__(timeout=300)
        self.boosts = boosts_with_qty
        self.page = page
        self.per_page = per_page
        self.total_pages = max(1, (len(boosts_with_qty) + per_page - 1) // per_page)

    @classmethod
    async def create(cls, interaction: discord.Interaction):
        boosts = form_store.get(interaction.user.id).get("selected_boosts", [])
        return cls(boosts)

    def _build_controls(self, all_items_map: dict):
        self.clear_items()
        start = self.page * self.per_page
        chunk = self.boosts[start:start + self.per_page]

        for idx, b in enumerate(chunk):
            global_idx = start + idx
            it = all_items_map.get(b["name"].lower())
            e = resolve_emoji(it.get("emoji", ""), getattr(self, '_guild', None)) if it else ""
            emoji_str = e + " " if e else ""
            label_prefix = f"{emoji_str}{b['name']}"

            minus_btn = discord.ui.Button(
                label="➖",
                style=discord.ButtonStyle.secondary,
                custom_id=f"qty_minus_{global_idx}",
                row=idx,
            )
            minus_btn.callback = lambda i, gi=global_idx: self._on_minus(i, gi)
            self.add_item(minus_btn)

            edit_btn = discord.ui.Button(
                label="✏️ Изменить",
                style=discord.ButtonStyle.primary,
                custom_id=f"qty_edit_{global_idx}",
                row=idx,
            )
            edit_btn.callback = lambda i, gi=global_idx: self._on_edit(i, gi)
            self.add_item(edit_btn)

            plus_btn = discord.ui.Button(
                label="➕",
                style=discord.ButtonStyle.secondary,
                custom_id=f"qty_plus_{global_idx}",
                row=idx,
            )
            plus_btn.callback = lambda i, gi=global_idx: self._on_plus(i, gi)
            self.add_item(plus_btn)

        nav_row = min(len(chunk), 4)
        if self.total_pages > 1:
            prev_btn = discord.ui.Button(
                label="◀",
                style=discord.ButtonStyle.secondary,
                custom_id="qty_prev",
                row=nav_row,
            )
            prev_btn.callback = self._on_prev
            self.add_item(prev_btn)

        confirm_btn = discord.ui.Button(
            label="✅ Подтвердить и отправить",
            style=discord.ButtonStyle.success,
            custom_id="qty_confirm",
            row=nav_row,
        )
        confirm_btn.callback = self._on_confirm
        self.add_item(confirm_btn)

        if self.total_pages > 1:
            next_btn = discord.ui.Button(
                label="▶",
                style=discord.ButtonStyle.secondary,
                custom_id="qty_next",
                row=nav_row,
            )
            next_btn.callback = self._on_next
            self.add_item(next_btn)

    async def _refresh(self, interaction: discord.Interaction):
        items_map = {}
        all_items = await asyncio.to_thread(interaction.client.repo.get_all_items)
        for it in all_items:
            items_map[it["name"].lower()] = it
        self._guild = interaction.guild
        self._build_controls(items_map)
        embed = self._build_embed(interaction, items_map)
        await interaction.response.edit_message(embed=embed, view=self)

    def _build_embed(self, interaction: discord.Interaction, items_map: dict = None) -> discord.Embed:
        if items_map is None:
            items_map = {}
        embed = discord.Embed(
            title="Настройка количества бустов",
            colour=discord.Colour.blurple(),
        )

        for b in self.boosts:
            it = items_map.get(b["name"].lower())
            e = resolve_emoji(it.get("emoji", ""), interaction.guild) if it else ""
            emoji_str = e + " " if e else ""
            price = it.get("price_sell") if it else 0
            line_total = (price or 0) * b["quantity"]
            embed.add_field(
                name=f"{emoji_str}{b['name']}",
                value=f"➖      ✏️ Изменить      ➕\nx{b['quantity']}",
                inline=False,
            )

        total = sum(
            (items_map.get(b["name"].lower(), {}).get("price_sell", 0) or 0) * b["quantity"]
            for b in self.boosts
        )
        embed.add_field(name="Общая стоимость", value=f"{_fmt(total)} ₽", inline=False)

        if self.total_pages > 1:
            embed.set_footer(text=f"Страница {self.page + 1} / {self.total_pages}")
        else:
            embed.set_footer(text="Нажмите ✏️ чтобы изменить количество вручную")

        form_store.set(interaction.user.id, "total_price", total)
        return embed

    async def _on_minus(self, interaction: discord.Interaction, index: int):
        if 0 <= index < len(self.boosts) and self.boosts[index]["quantity"] > 1:
            self.boosts[index]["quantity"] -= 1
            form_store.set(interaction.user.id, "selected_boosts", self.boosts)
        await self._refresh(interaction)

    async def _on_plus(self, interaction: discord.Interaction, index: int):
        if 0 <= index < len(self.boosts):
            self.boosts[index]["quantity"] += 1
            form_store.set(interaction.user.id, "selected_boosts", self.boosts)
        await self._refresh(interaction)

    async def _on_edit(self, interaction: discord.Interaction, index: int):
        if 0 <= index < len(self.boosts):
            current_qty = self.boosts[index]["quantity"]
            boost_name = self.boosts[index]["name"]
            await interaction.response.send_modal(QuantityEditModal(index, current_qty, boost_name))

    async def _on_prev(self, interaction: discord.Interaction):
        self.page = (self.page - 1) % self.total_pages
        items_map = {}
        all_items = await asyncio.to_thread(interaction.client.repo.get_all_items)
        for it in all_items:
            items_map[it["name"].lower()] = it
        self._guild = interaction.guild
        self._build_controls(items_map)
        embed = self._build_embed(interaction, items_map)
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_next(self, interaction: discord.Interaction):
        self.page = (self.page + 1) % self.total_pages
        items_map = {}
        all_items = await asyncio.to_thread(interaction.client.repo.get_all_items)
        for it in all_items:
            items_map[it["name"].lower()] = it
        self._guild = interaction.guild
        self._build_controls(items_map)
        embed = self._build_embed(interaction, items_map)
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_confirm(self, interaction: discord.Interaction):
        items_map = {}
        all_items = await asyncio.to_thread(interaction.client.repo.get_all_items)
        for it in all_items:
            items_map[it["name"].lower()] = it
        total = sum(
            (items_map.get(b["name"].lower(), {}).get("price_sell", 0) or 0) * b["quantity"]
            for b in self.boosts
        )
        enriched = []
        for b in self.boosts:
            it = items_map.get(b["name"].lower())
            price = it.get("price_sell") if it else None
            qty = b.get("quantity", 1)
            enriched.append({"name": b["name"], "quantity": qty, "price": price, "line_total": (price or 0) * qty})
        form_store.set(interaction.user.id, "selected_boosts", enriched)
        form_store.set(interaction.user.id, "total_price", total)

        store = form_store.get(interaction.user.id)
        edit_message_id = store.get("edit_message_id")
        edit_request_data = store.get("edit_request_data")

        if edit_message_id and edit_request_data:
            edit_request_data["selected_boosts"] = enriched
            edit_request_data["total_price"] = total

            text_data = edit_request_data.get("text_data", {})
            delivery = edit_request_data.get("delivery_method", "")
            category = edit_request_data.get("category", "Заказ бустов")

            embed = _build_request_card_embed(interaction, text_data, delivery, enriched, total, category)

            await _save_request_meta(interaction.channel_id, edit_message_id, interaction.user.id, edit_request_data)

            try:
                msg = await interaction.channel.fetch_message(edit_message_id)
                files = []
                for a in msg.attachments:
                    if a.content_type and a.content_type.startswith("image/"):
                        fp = await a.to_file()
                        files.append(fp)
                kwargs = {"embed": embed, "view": EditRequestView()}
                if files:
                    kwargs["attachments"] = files[:1]
                await msg.edit(**kwargs)

                await interaction.response.edit_message(content="✅ Заявка обновлена.", embed=None, view=None)
            except (discord.NotFound, discord.HTTPException) as e:
                await interaction.followup.send("⚠️ Не удалось найти заявку для редактирования.", ephemeral=True)
                logger.warning("Edit failed: message %s not found: %s", edit_message_id, e)

            form_store.clear(interaction.user.id)
        else:
            text_data = store.get("text_data", {})
            delivery = store.get("delivery_method", "")
            category = store.get("category", "Заказ бустов")

            modal = BoostOrderModal(category)
            await interaction.response.defer(ephemeral=True)
            await modal._publish(interaction)


# ------------------------------------------------------------------ #
#  Screenshot Prompt                                                 #
# ------------------------------------------------------------------ #

class ScreenshotPromptView(discord.ui.View):

    def __init__(self, user_id: int, target_message: discord.Message, original_embed: discord.Embed):
        super().__init__(timeout=120)
        self.user_id = user_id
        self.target_message = target_message
        self.original_embed = original_embed

    @discord.ui.button(label="📎 Жду скриншот", style=discord.ButtonStyle.primary, custom_id="screenshot_wait")
    async def wait_screenshot(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Это не ваша заявка.", ephemeral=True)
            return
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content="📷 **Отправьте изображение в этот чат.** После получения скриншот будет добавлен к заявке.",
            view=self,
        )

        def check(m: discord.Message) -> bool:
            return m.author.id == self.user_id and m.channel.id == interaction.channel_id

        try:
            msg = await interaction.client.wait_for("message", timeout=120.0, check=check)
        except asyncio.TimeoutError:
            try:
                await interaction.edit_original_response(content="⏱ Время ожидания скриншота истекло.")
            except Exception:
                pass
            return

        image_files = []
        for att in msg.attachments:
            if att.content_type and att.content_type.startswith("image/"):
                fp = await att.to_file()
                image_files.append(fp)

        if not image_files:
            try:
                await msg.reply("❌ Пожалуйста, прикрепите файл с изображением.", delete_after=10)
            except Exception:
                pass
            for child in self.children:
                child.disabled = False
            try:
                await interaction.edit_original_response(
                    content="📷 **Прикрепите изображение следующим сообщением.**",
                    view=self,
                )
            except Exception:
                pass
            return

        self.original_embed.set_image(url=f"attachment://{image_files[0].filename}")
        try:
            await self.target_message.edit(embed=self.original_embed, attachments=image_files[:1])
            if len(image_files) > 1:
                await self.target_message.reply(
                    f"📎 **Дополнительные скриншоты:** прикреплено ещё {len(image_files) - 1} файл(ов).",
                )
        except (discord.HTTPException, discord.Forbidden) as e:
            logger.warning("Failed to attach screenshot: %s", e)
            try:
                await interaction.followup.send("⚠️ Не удалось прикрепить скриншот.", ephemeral=True)
            except Exception:
                pass

        try:
            await interaction.edit_original_response(content="✅ Скриншот прикреплён.", view=None)
        except Exception:
            pass

        try:
            await msg.delete()
        except Exception:
            pass

    @discord.ui.button(label="⏭ Пропустить", style=discord.ButtonStyle.secondary, custom_id="screenshot_skip")
    async def skip_screenshot(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Это не ваша заявка.", ephemeral=True)
            return
        await interaction.response.edit_message(content="✅ Заявка опубликована без скриншота.", view=None)


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
