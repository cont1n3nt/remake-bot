import asyncio
import csv
import io
import logging
import math
import os
import re
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot.repositories.sheets_repository import SheetsRepository
from bot.services.ocr_service import _fmt
from bot.utils.embeds import error_embed, resolve_emoji, format_price_change
from bot.utils.parsing import parse_ruble_amount
from bot.cogs.items import _sync_prices_from_db

logger = logging.getLogger("bot")

DATA_START_ROW = 3


# ------------------------------------------------------------------ #
#  Price List View (с переключением ресурсы/бусты)                   #
# ------------------------------------------------------------------ #

class PriceListView(discord.ui.View):
    def __init__(self, resources: list[dict], boosts: list[dict],
                 page: int = 0, show_resources: bool = True,
                 guild: Optional[discord.Guild] = None):
        super().__init__(timeout=120)
        self.resources = resources
        self.boosts = boosts
        self.page = page
        self.show_resources = show_resources
        self.per_page = 15
        self._guild = guild

    def _build_embed(self) -> discord.Embed:
        items = self.resources if self.show_resources else self.boosts
        label = "Ресурсы (Скупка)" if self.show_resources else "Бусты (Продажа)"
        start = self.page * self.per_page
        chunk = items[start:start + self.per_page]
        embed = discord.Embed(
            title=f"📋 Прайс-лист — {label}",
            colour=discord.Colour.blue(),
        )
        total_pages = max(1, math.ceil(len(items) / self.per_page))
        for it in chunk:
            price = _fmt(it.get("price_buy", 0)) if self.show_resources else _fmt(it.get("price_sell", 0))
            e = resolve_emoji(it.get("emoji", ""), self._guild)
            emoji = e + " " if e else ""
            embed.add_field(name=f"{emoji}{it['name']}", value=f"{price} ₽", inline=True)
        embed.set_footer(text=f"Страница {self.page + 1}/{total_pages} • Всего: {len(items)}")
        return embed

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary, custom_id="admin_price_prev")
    async def prev(self, i: discord.Interaction, _b: discord.ui.Button):
        items = self.resources if self.show_resources else self.boosts
        total = max(1, math.ceil(len(items) / self.per_page))
        self.page = (self.page - 1) % total
        await i.response.edit_message(embed=self._build_embed(), view=self)

    @discord.ui.button(label="🔄 Сменить категорию", style=discord.ButtonStyle.primary, custom_id="admin_price_toggle")
    async def toggle(self, i: discord.Interaction, _b: discord.ui.Button):
        self.show_resources = not self.show_resources
        self.page = 0
        await i.response.edit_message(embed=self._build_embed(), view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary, custom_id="admin_price_next")
    async def nxt(self, i: discord.Interaction, _b: discord.ui.Button):
        items = self.resources if self.show_resources else self.boosts
        total = max(1, math.ceil(len(items) / self.per_page))
        self.page = (self.page + 1) % total
        await i.response.edit_message(embed=self._build_embed(), view=self)


class AdminCmdsCog(commands.Cog):

    def __init__(self, bot: commands.Bot, repo: SheetsRepository) -> None:
        self.bot = bot
        self._repo = repo

    # ------------------------------------------------------------------
    #  LogsView — пагинация для /logs
    # ------------------------------------------------------------------

    class LogsView(discord.ui.View):
        def __init__(self, lines: list[str], per_page: int = 20):
            super().__init__(timeout=180)
            self.lines = lines
            self.per_page = per_page
            self.page = 0
            self.total_pages = max(1, (len(lines) + per_page - 1) // per_page)

        def _build_text(self) -> str:
            start = self.page * self.per_page
            chunk = self.lines[start:start + self.per_page]
            text = "```\n" + "\n".join(chunk) + "```"
            return text

        @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary, custom_id="logs_prev")
        async def prev(self, i: discord.Interaction, _b: discord.ui.Button):
            self.page = (self.page - 1) % self.total_pages
            await i.response.edit_message(content=self._build_text(), view=self)

        @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary, custom_id="logs_next")
        async def nxt(self, i: discord.Interaction, _b: discord.ui.Button):
            self.page = (self.page + 1) % self.total_pages
            await i.response.edit_message(content=self._build_text(), view=self)

    # ------------------------------------------------------------------
    #  /logs
    # ------------------------------------------------------------------
    @app_commands.command(name="logs", description="📜 (Админ) Вывести полные логи всех совершенных сделок от новых к старым")
    @app_commands.checks.has_permissions(administrator=True)
    async def logs(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            vals = await asyncio.to_thread(self._repo.get_transactions)
        except Exception as e:
            await interaction.followup.send(embed=error_embed(f"Ошибка: {e}"), ephemeral=True)
            return
        lines = []
        day_counters = {}
        for row in reversed(vals):
            if len(row) < 5:
                continue
            raw_date = str(row[0]).strip() if row[0] else ""
            if not raw_date:
                continue
            nick = str(row[1]).strip() if len(row) > 1 else ""
            is_buy = len(row) > 2 and str(row[2]).strip().upper() == "TRUE"
            is_sell = len(row) > 3 and str(row[3]).strip().upper() == "TRUE"
            if not is_buy and not is_sell:
                continue
            t = "Покупка" if is_buy else "Продажа"
            amt = parse_ruble_amount(row[4]) if len(row) > 4 and row[4] else 0.0
            if amt == 0.0:
                continue
            ref = str(row[7]).strip() if len(row) > 7 and row[7] else ""
            day_key = raw_date[:10]
            day_counters[day_key] = day_counters.get(day_key, 0) + 1
            ticket_num = day_counters[day_key]
            line = f"№{ticket_num} [{raw_date}] {nick} | {t} | {_fmt(amt)}₽"
            if ref:
                line += f" | Реферер: {ref}"
            lines.append(line)
        if not lines:
            await interaction.followup.send("Нет записей.", ephemeral=True)
            return
        view = self.LogsView(lines)
        text = view._build_text()
        await interaction.followup.send(text, view=view, ephemeral=True)

    @logs.error
    async def logs_error(self, i: discord.Interaction, e: app_commands.AppCommandError):
        if isinstance(e, app_commands.MissingPermissions):
            try: await i.response.send_message("Недостаточно прав. Требуются права администратора.", ephemeral=True)
            except: await i.followup.send("Недостаточно прав. Требуются права администратора.", ephemeral=True)

    # ------------------------------------------------------------------
    #  /give_price
    # ------------------------------------------------------------------
    @app_commands.command(name="give_price", description="📥 (Админ) Скачать файл с текущими ценами")
    @app_commands.checks.has_permissions(administrator=True)
    async def give_price(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            items = await asyncio.to_thread(self._repo.get_all_items)
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["ID", "Название", "Категория", "Цена скупки", "Цена продажи", "Эмодзи", "Обновлено"])
            for it in items:
                w.writerow([
                    it.get("id", ""),
                    it.get("name", ""),
                    it.get("category", ""),
                    _fmt(it["price_buy"]) if it.get("price_buy") else "",
                    _fmt(it["price_sell"]) if it.get("price_sell") else "",
                    it.get("emoji", ""),
                    it.get("updated_at", ""),
                ])
            buf.seek(0)
            file = discord.File(io.BytesIO(buf.getvalue().encode("utf-8-sig")), filename="prices.csv")
            await interaction.followup.send(file=file, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(embed=error_embed(f"Ошибка: {e}"), ephemeral=True)

    @give_price.error
    async def give_price_error(self, i: discord.Interaction, e: app_commands.AppCommandError):
        if isinstance(e, app_commands.MissingPermissions):
            try: await i.response.send_message("Недостаточно прав.", ephemeral=True)
            except: await i.followup.send("Недостаточно прав.", ephemeral=True)

    # ------------------------------------------------------------------
    #  /price_list
    # ------------------------------------------------------------------
    @app_commands.command(name="price_list", description="📋 (Админ) Показать прайс-лист ресурсов с кнопкой переключения на бусты")
    @app_commands.checks.has_permissions(administrator=True)
    async def price_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            items = await asyncio.to_thread(self._repo.get_all_items)
            resources = [it for it in items if it["category"] == "resource"]
            boosts = [it for it in items if it["category"] == "boost"]
            view = PriceListView(resources, boosts, guild=interaction.guild)
            await interaction.followup.send(embed=view._build_embed(), view=view, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(embed=error_embed(f"Ошибка: {e}"), ephemeral=True)

    @price_list.error
    async def price_list_error(self, i: discord.Interaction, e: app_commands.AppCommandError):
        if isinstance(e, app_commands.MissingPermissions):
            try: await i.response.send_message("Недостаточно прав.", ephemeral=True)
            except: await i.followup.send("Недостаточно прав.", ephemeral=True)

    # ------------------------------------------------------------------
    #  /new_price
    # ------------------------------------------------------------------
    @app_commands.command(name="new_price", description="📁 (Админ) Загрузить новый прайс-лист для массового обновления (.csv/.txt)")
    @app_commands.describe(file="CSV/TXT-файл с колонками: Название, Категория, Цена скупки, Цена продажи, Эмодзи")
    @app_commands.checks.has_permissions(administrator=True)
    async def new_price(self, interaction: discord.Interaction, file: discord.Attachment):
        await interaction.response.defer(ephemeral=True)
        if not (file.filename.endswith(".csv") or file.filename.endswith(".txt")):
            await interaction.followup.send(embed=error_embed("Файл должен быть в формате .csv или .txt."), ephemeral=True)
            return
        try:
            content = (await file.read()).decode("utf-8-sig")
            if file.filename.endswith(".txt"):
                lines = [l for l in content.split("\n") if l.strip()]
                fieldnames = ["Название", "Категория", "Цена скупки", "Цена продажи", "Эмодзи"]
                reader = csv.DictReader(lines, fieldnames=fieldnames)
            else:
                reader = csv.DictReader(io.StringIO(content))

            all_items = await asyncio.to_thread(self._repo.get_all_items)
            old_items_by_name_cat = {(it["name"].lower(), it["category"].lower()): it for it in all_items}
            old_items_by_name = {}
            for it in all_items:
                key = it["name"].lower()
                if key not in old_items_by_name:
                    old_items_by_name[key] = it
            guild = interaction.guild

            count = 0
            changes_resources = []
            changes_boosts = []
            changes_skup_boost = []
            for row in reader:
                name = row.get("Название", "").strip()
                cat = row.get("Категория", "").strip()
                if not name or not cat:
                    continue
                pb_raw = row.get("Цена скупки", "").strip()
                ps_raw = row.get("Цена продажи", "").strip()
                pb = float(pb_raw.replace(" ", "").replace(",", ".").replace("₽", "")) if pb_raw else None
                ps = float(ps_raw.replace(" ", "").replace(",", ".").replace("₽", "")) if ps_raw else None
                emoji = row.get("Эмодзи", "").strip()
                old = old_items_by_name_cat.get((name.lower(), cat.lower()))
                if old is None:
                    old_by_name = old_items_by_name.get(name.lower())
                    if old_by_name is not None and old_by_name["category"].lower() != cat.lower():
                        await interaction.followup.send(
                            f"⚠️ Пропущен «{name}»: указана категория «{cat}», "
                            f"но в базе категория «{old_by_name['category']}». Категорию менять запрещено.",
                            ephemeral=True,
                        )
                        continue
                old_pb = old.get("price_buy") if old else None
                old_ps = old.get("price_sell") if old else None
                existing_cat = old["category"] if old else cat
                await asyncio.to_thread(
                    self._repo.upsert_item, name, existing_cat,
                    price_buy=pb, price_sell=ps, emoji=emoji,
                )
                count += 1
                if old and (old_pb != pb or old_ps != ps):
                    item_for_line = {"name": name, "emoji": old.get("emoji", "")}
                    if old_pb != pb and pb is not None and pb != 0:
                        line = format_price_change(item_for_line, old_pb, pb, guild=guild)
                        if existing_cat == "resource":
                            changes_resources.append(line)
                        elif existing_cat == "boost":
                            changes_boosts.append(line)
                        else:
                            changes_skup_boost.append(line)
                    if old_ps != ps and ps is not None and ps != 0:
                        line = format_price_change(item_for_line, old_ps, ps, guild=guild)
                        if existing_cat == "resource":
                            changes_resources.append(line)
                        elif existing_cat == "boost":
                            changes_boosts.append(line)
                        else:
                            changes_skup_boost.append(line)
            await asyncio.to_thread(_sync_prices_from_db, self._repo)
            msg = f"✅ Импортировано {count} позиций."

            audit_all = []

            if changes_resources:
                embed = discord.Embed(
                    title="📦 Изменение цен ресурсов",
                    colour=discord.Colour.blue(),
                )
                embed.description = "\n".join(changes_resources)
                await interaction.followup.send(embed=embed, ephemeral=True)
                audit_all.extend(changes_resources)

            if changes_boosts:
                embed = discord.Embed(
                    title="🚀 Изменение цен бустов",
                    colour=discord.Colour.blue(),
                )
                embed.description = "\n".join(changes_boosts)
                await interaction.followup.send(embed=embed, ephemeral=True)
                audit_all.extend(changes_boosts)

            if changes_skup_boost:
                embed = discord.Embed(
                    title="📦 Изменение цен скупа бустов",
                    colour=discord.Colour.blue(),
                )
                embed.description = "\n".join(changes_skup_boost)
                await interaction.followup.send(embed=embed, ephemeral=True)
                audit_all.extend(changes_skup_boost)

            await interaction.followup.send(msg, ephemeral=True)
            try:
                audit = interaction.client.audit_logger
                await audit.log(interaction.user, "/new_price", audit_all)
            except Exception:
                pass
        except Exception as e:
            await interaction.followup.send(embed=error_embed(f"Ошибка: {e}"), ephemeral=True)

    @new_price.error
    async def new_price_error(self, i: discord.Interaction, e: app_commands.AppCommandError):
        if isinstance(e, app_commands.MissingPermissions):
            try: await i.response.send_message("Недостаточно прав.", ephemeral=True)
            except: await i.followup.send("Недостаточно прав.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCmdsCog(bot, bot.repo))
