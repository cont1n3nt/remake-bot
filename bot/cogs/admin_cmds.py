import asyncio
import csv
import io
import json
import logging
import math
import os
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot.repositories.sheets_repository import SheetsRepository
from bot.utils.embeds import error_embed

logger = logging.getLogger("bot")

PRICES_FILE = "prices.json"
DATA_START_ROW = 3


def _fmt(n: float) -> str:
    if n == int(n):
        return str(int(n))
    return f"{n:.2f}".rstrip("0").rstrip(".")


# ------------------------------------------------------------------ #
#  Price List View (с переключением ресурсы/бусты)                   #
# ------------------------------------------------------------------ #

class PriceListView(discord.ui.View):
    def __init__(self, resources: list[dict], boosts: list[dict],
                 page: int = 0, show_resources: bool = True):
        super().__init__(timeout=120)
        self.resources = resources
        self.boosts = boosts
        self.page = page
        self.show_resources = show_resources
        self.per_page = 15

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
            emoji = it.get("emoji", "") + " " if it.get("emoji") else ""
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
        seen = set()
        for row in reversed(vals):
            if len(row) < 5:
                continue
            key = tuple(row)
            if key in seen:
                continue
            seen.add(key)
            raw_date = str(row[0]).strip() if row[0] else ""
            nick = str(row[1]).strip() if len(row) > 1 else ""
            t = "Покупка" if len(row) > 2 and str(row[2]).strip().upper() == "TRUE" else "Продажа"
            amt = 0.0
            try:
                amt = float(str(row[4]).strip().replace(" ", "").replace(",", ".")) if len(row) > 4 and row[4] else 0.0
            except ValueError:
                pass
            ref = str(row[7]).strip() if len(row) > 7 and row[7] else ""
            line = f"[{raw_date}] {nick} | {t} | {_fmt(amt)}₽"
            if ref:
                line += f" | Реферер: {ref}"
            lines.append(line)
            if len(lines) >= 50:
                break
        if not lines:
            await interaction.followup.send("Нет записей.", ephemeral=True)
            return
        text = "```\n" + "\n".join(lines) + "```"
        if len(text) > 1900:
            text = "```\n" + "\n".join(lines[:30]) + f"\n… и ещё {len(lines) - 30} записей" + "```"
        await interaction.followup.send(text, ephemeral=True)

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
            view = PriceListView(resources, boosts)
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
    @app_commands.command(name="new_price", description="📁 (Админ) Загрузить новый прайс-лист для массового обновления")
    @app_commands.describe(file="CSV-файл с колонками: Название, Категория, Цена скупки, Цена продажи, Эмодзи")
    @app_commands.checks.has_permissions(administrator=True)
    async def new_price(self, interaction: discord.Interaction, file: discord.Attachment):
        await interaction.response.defer(ephemeral=True)
        if not file.filename.endswith(".csv"):
            await interaction.followup.send(embed=error_embed("Файл должен быть в формате CSV."), ephemeral=True)
            return
        try:
            content = (await file.read()).decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(content))
            count = 0
            for row in reader:
                name = row.get("Название", "").strip()
                cat = row.get("Категория", "").strip()
                pb_raw = row.get("Цена скупки", "").strip()
                ps_raw = row.get("Цена продажи", "").strip()
                pb = float(pb_raw.replace(" ", "").replace(",", ".")) if pb_raw else None
                ps = float(ps_raw.replace(" ", "").replace(",", ".")) if ps_raw else None
                emoji = row.get("Эмодзи", "").strip()
                if name and cat:
                    await asyncio.to_thread(
                        self._repo.upsert_item, name, cat,
                        price_buy=pb, price_sell=ps, emoji=emoji,
                    )
                    count += 1
            # sync prices
            await asyncio.to_thread(_sync_prices_from_db, self._repo)
            await interaction.followup.send(f"✅ Импортировано {count} позиций.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(embed=error_embed(f"Ошибка: {e}"), ephemeral=True)

    @new_price.error
    async def new_price_error(self, i: discord.Interaction, e: app_commands.AppCommandError):
        if isinstance(e, app_commands.MissingPermissions):
            try: await i.response.send_message("Недостаточно прав.", ephemeral=True)
            except: await i.followup.send("Недостаточно прав.", ephemeral=True)


def _sync_prices_from_db(repo: SheetsRepository) -> dict[str, float]:
    items = repo.get_all_items()
    prices = {}
    for it in items:
        if it.get("price_buy") is not None:
            prices[it["name"]] = it["price_buy"]
        if it.get("price_sell") is not None:
            prices[f"{it['name']} (boost)"] = it["price_sell"]
    with open(PRICES_FILE, "w", encoding="utf-8") as f:
        json.dump(prices, f, ensure_ascii=False, indent=2)
    return prices


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCmdsCog(bot, bot.repo))
