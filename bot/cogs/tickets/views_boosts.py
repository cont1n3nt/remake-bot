"""Шаги 3–4 визарда тикета: выбор бустов и настройка их количества.

Перенесено из bot/cogs/tickets.py без изменений (REFACTORING_PLAN.md,
Этап F.4b).

⚠ `BoostQuantityView._on_confirm` — единственное место во всей Фазе F,
где импорт намеренно НЕ в шапке файла: `BoostOrderModal` (views_delivery.py)
и `EditRequestView` (views_edit.py) импортируются локально внутри метода,
чтобы разорвать циклическую зависимость views_delivery ↔ views_boosts ↔
views_edit (см. REFACTOR_PROGRESS.md, Фаза F, F.4a — полная схема)."""

import asyncio
import logging

import discord

from bot.services.ocr_service import _fmt
from bot.utils.embeds import resolve_emoji
from bot.cogs.tickets.embeds import _build_request_card_embed
from bot.cogs.tickets.storage import form_store, _save_request_meta

logger = logging.getLogger("bot")


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

        # Кнопки «➕ Добавить» здесь нет (пункт 2): состав правится прямо в
        # выпадающем списке, а количество — на следующем шаге.
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

    def _sync_select_defaults(self) -> None:
        """Держать галочки в выпадающем списке в согласии с self._selected."""
        for option in self._boost_select.options:
            option.default = option.value in self._selected

    async def _update_message(self, interaction: discord.Interaction):
        selected_names = self._get_selected_names()
        if selected_names:
            lines = "\n".join(f"• {n}" for n in selected_names)
            content = f"**Выбранные бусты:**\n{lines}\n\n**Нажмите ✅ Далее**"
        else:
            content = "**Выберите нужные бусты:**"
        self._sync_select_defaults()
        await interaction.response.edit_message(content=content, view=self)

    async def _on_select(self, interaction: discord.Interaction):
        # Выбор фиксируется сразу: отдельной кнопки «Добавить» больше нет,
        # список в меню и есть итоговый состав заявки (пункт 2).
        self._selected = list(self._boost_select.values)
        form_store.set(
            interaction.user.id, "selected_boosts",
            [{"name": n, "quantity": 1} for n in self._selected],
        )
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
        # Именно здесь раньше терялись кнопки: view отдавался без _build_controls(),
        # и пользователь получал пустое сообщение без «✅ Подтвердить» (пункт 12).
        await view.render(interaction, content="**Настройте количество каждого буста:**")


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
        await view.render(interaction, content="**Настройте количество каждого буста:**")


class BoostQuantityView(discord.ui.View):

    def __init__(self, boosts_with_qty: list[dict], page: int = 0, per_page: int = 4):
        super().__init__(timeout=300)
        self.boosts = boosts_with_qty
        self.page = page
        self.per_page = per_page
        self.total_pages = max(1, (len(boosts_with_qty) + per_page - 1) // per_page)
        # Второй клик по «Подтвердить» публиковал вторую карточку (пункт 8).
        self._submitting = False

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

    @staticmethod
    async def _load_items_map(interaction: discord.Interaction) -> dict:
        all_items = await asyncio.to_thread(interaction.client.repo.get_all_items)
        return {it["name"].lower(): it for it in all_items}

    async def render(self, interaction: discord.Interaction, content: str | None = None):
        """Единственный способ показать это представление.

        Раньше кнопки строились только внутри _refresh/_on_prev/_on_next, а два
        других места отдавали view без них — отсюда «кнопка подтвердить иногда
        не работает» (пункт 12). Теперь любая отрисовка идёт через этот метод.
        """
        items_map = await self._load_items_map(interaction)
        self._guild = interaction.guild
        self._build_controls(items_map)
        embed = self._build_embed(interaction, items_map)
        kwargs = {"embed": embed, "view": self}
        if content is not None:
            kwargs["content"] = content
        await interaction.response.edit_message(**kwargs)

    async def _refresh(self, interaction: discord.Interaction):
        await self.render(interaction)

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
                value=f"Количество: {b['quantity']}\nСтоимость: {_fmt(line_total)} ₽",
                inline=False,
            )

        total = sum(
            (items_map.get(b["name"].lower(), {}).get("price_sell", 0) or 0) * b["quantity"]
            for b in self.boosts
        )
        embed.add_field(name="💰 Общая стоимость", value=f"{_fmt(total)} ₽", inline=False)

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
        await self.render(interaction)

    async def _on_next(self, interaction: discord.Interaction):
        self.page = (self.page + 1) % self.total_pages
        await self.render(interaction)

    async def _on_confirm(self, interaction: discord.Interaction):
        if self._submitting:
            await interaction.response.send_message("Заявка уже отправляется…", ephemeral=True)
            return
        self._submitting = True
        for child in self.children:
            child.disabled = True

        items_map = await self._load_items_map(interaction)
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
            from bot.cogs.tickets.views_edit import EditRequestView, update_request_log

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
                kwargs = {
                    "embed": embed,
                    "view": EditRequestView(),
                    "allowed_mentions": discord.AllowedMentions.none(),
                }
                if files:
                    kwargs["attachments"] = files[:1]
                await msg.edit(**kwargs)

                await interaction.response.edit_message(content="✅ Заявка обновлена.", embed=None, view=None)
            except (discord.NotFound, discord.HTTPException) as e:
                await interaction.followup.send("⚠️ Не удалось найти заявку для редактирования.", ephemeral=True)
                logger.warning("Edit failed: message %s not found: %s", edit_message_id, e)

            # Обновляем тот же лог, а не шлём новый (пункт 14).
            await update_request_log(interaction, edit_request_data)
            form_store.clear(interaction.user.id)
        else:
            text_data = store.get("text_data", {})
            delivery = store.get("delivery_method", "")
            category = store.get("category", "Заказ бустов")

            from bot.cogs.tickets.views_delivery import BoostOrderModal
            modal = BoostOrderModal(category)
            await interaction.response.defer(ephemeral=True)
            await modal._publish(interaction)
