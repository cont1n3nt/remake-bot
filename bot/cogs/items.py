import asyncio
import json
import logging
import os
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot.repositories.sheets_repository import SheetsRepository
from bot.utils.calculator import safe_calc
from bot.utils.embeds import error_embed, resolve_emoji

logger = logging.getLogger("bot")

PRICES_FILE = "prices.json"


class ItemsCog(commands.Cog):

    def __init__(self, bot: commands.Bot, repo: SheetsRepository) -> None:
        self.bot = bot
        self._repo = repo

    @staticmethod
    def _clean_choice_name(it: dict) -> str:
        """Return clean item name without emoji codes for dropdown display."""
        return it["name"]

    async def _autocomplete_items(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice]:
        items = await asyncio.to_thread(self._repo.get_all_items)
        matches = [it for it in items if current.lower() in it["name"].lower()]
        return [
            app_commands.Choice(
                name=self._clean_choice_name(it),
                value=it["name"],
            )
            for it in matches[:25]
        ]

    async def _autocomplete_resources(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice]:
        items = [it for it in (await asyncio.to_thread(self._repo.get_all_items)) if it["category"] == "resource"]
        matches = [it for it in items if current.lower() in it["name"].lower()]
        return [
            app_commands.Choice(
                name=self._clean_choice_name(it),
                value=it["name"],
            )
            for it in matches[:25]
        ]

    async def _autocomplete_boosts(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice]:
        items = [it for it in (await asyncio.to_thread(self._repo.get_all_items)) if it["category"] == "boost"]
        matches = [it for it in items if current.lower() in it["name"].lower()]
        return [
            app_commands.Choice(
                name=self._clean_choice_name(it),
                value=it["name"],
            )
            for it in matches[:25]
        ]

    def _format_price_change(self, it: dict, old_price: Optional[float], new_price: float, guild: Optional[discord.Guild] = None) -> str:
        e = resolve_emoji(it.get("emoji", ""), guild)
        emoji_str = e + " " if e else ""
        old_str = _fmt(old_price) if old_price is not None else "—"
        return f"• {emoji_str}{it['name']} | {old_str} ₽ → {_fmt(new_price)} ₽"

    @app_commands.command(name="setprice", description="🏷️ (Админ) Изменить цену ресурса или скупки буста в базе данных и таблице")
    @app_commands.describe(item="Название предмета", price="Новая цена скупки (поддерживает выражения)")
    @app_commands.autocomplete(item=_autocomplete_resources)
    @app_commands.checks.has_permissions(administrator=True)
    async def setprice(self, interaction: discord.Interaction, item: str, price: str):
        await interaction.response.defer(ephemeral=True)
        try:
            amount = safe_calc(price)
        except Exception:
            await interaction.followup.send(embed=error_embed("Некорректная цена."), ephemeral=True)
            return
        try:
            it = await asyncio.to_thread(self._repo.find_item_by_name_and_category, item, "resource")
            if it is None:
                await interaction.followup.send(embed=error_embed("Предмет не найден в категории resource."), ephemeral=True)
                return
            old_price = it.get("price_buy")
            await asyncio.to_thread(self._repo.upsert_item, it["name"], it["category"], price_buy=amount)
            await asyncio.to_thread(_sync_prices_from_db, self._repo)
            change = self._format_price_change(it, old_price, amount, guild=interaction.guild)
            await interaction.followup.send(change, ephemeral=True)
            try:
                audit = interaction.client.audit_logger
                details = {
                    "Предмет": f"{it['name']} (resource)",
                    "Цена была": f"{_fmt(old_price) if old_price is not None else '—'} ₽",
                    "Цена стала": f"{_fmt(amount)} ₽",
                }
                await audit.log(interaction.user, "/setprice", details)
            except Exception:
                pass
        except Exception as e:
            await interaction.followup.send(embed=error_embed(f"Ошибка: {e}"), ephemeral=True)

    @setprice.error
    async def setprice_error(self, i: discord.Interaction, e: app_commands.AppCommandError):
        if isinstance(e, app_commands.MissingPermissions):
            try: await i.response.send_message("Недостаточно прав. Требуются права администратора.", ephemeral=True)
            except: await i.followup.send("Недостаточно прав. Требуются права администратора.", ephemeral=True)

    @app_commands.command(name="setboost", description="🍔 (Админ) Изменить цену продажи буста игрокам")
    @app_commands.describe(item="Название буста", price="Новая цена продажи (поддерживает выражения)")
    @app_commands.autocomplete(item=_autocomplete_boosts)
    @app_commands.checks.has_permissions(administrator=True)
    async def setboost(self, interaction: discord.Interaction, item: str, price: str):
        await interaction.response.defer(ephemeral=True)
        try:
            amount = safe_calc(price)
        except Exception:
            await interaction.followup.send(embed=error_embed("Некорректная цена."), ephemeral=True)
            return
        try:
            it = await asyncio.to_thread(self._repo.find_item_by_name_and_category, item, "boost")
            if it is None:
                await interaction.followup.send(embed=error_embed("Буст не найден в категории boost."), ephemeral=True)
                return
            old_price = it.get("price_sell")
            await asyncio.to_thread(self._repo.upsert_item, it["name"], it["category"], price_sell=amount)
            await asyncio.to_thread(_sync_prices_from_db, self._repo)
            change = self._format_price_change(it, old_price, amount, guild=interaction.guild)
            await interaction.followup.send(change, ephemeral=True)
            try:
                audit = interaction.client.audit_logger
                details = {
                    "Предмет": f"{it['name']} (boost)",
                    "Цена была": f"{_fmt(old_price) if old_price is not None else '—'} ₽",
                    "Цена стала": f"{_fmt(amount)} ₽",
                }
                await audit.log(interaction.user, "/setboost", details)
            except Exception:
                pass
        except Exception as e:
            await interaction.followup.send(embed=error_embed(f"Ошибка: {e}"), ephemeral=True)

    @setboost.error
    async def setboost_error(self, i: discord.Interaction, e: app_commands.AppCommandError):
        if isinstance(e, app_commands.MissingPermissions):
            try: await i.response.send_message("Недостаточно прав. Требуются права администратора.", ephemeral=True)
            except: await i.followup.send("Недостаточно прав. Требуются права администратора.", ephemeral=True)

    @app_commands.command(name="item_add", description="➕ (Админ) Добавить новый предмет или буст в общую базу данных")
    @app_commands.describe(
        name="Название предмета/буста",
        category="Тип: resource — скупка, boost — продажа",
        price_buy="Цена скупки (для resource, поддерживает выражения)",
        price_sell="Цена продажи (для boost, поддерживает выражения)",
        emoji="Эмодзи (необязательно)",
    )
    @app_commands.choices(category=[
        app_commands.Choice(name="Ресурс (скупка)", value="resource"),
        app_commands.Choice(name="Буст (продажа)", value="boost"),
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def item_add(self, interaction: discord.Interaction, name: str, category: str,
                       price_buy: Optional[str] = None, price_sell: Optional[str] = None, emoji: str = ""):
        await interaction.response.defer(ephemeral=True)
        try:
            pb = safe_calc(price_buy) if price_buy else None
        except Exception:
            pb = None
        try:
            ps = safe_calc(price_sell) if price_sell else None
        except Exception:
            ps = None
        try:
            it = await asyncio.to_thread(self._repo.upsert_item, name.strip(), category, price_buy=pb, price_sell=ps, emoji=emoji.strip())
            await asyncio.to_thread(_sync_prices_from_db, self._repo)
            e = resolve_emoji(it.get("emoji", ""), interaction.guild)
            emoji_str = e + " " if e else ""
            await interaction.followup.send(f"✅ {emoji_str}{it['name']} добавлен (ID: {it['id']})", ephemeral=True)
        except Exception as ex:
            await interaction.followup.send(embed=error_embed(f"Ошибка: {ex}"), ephemeral=True)

    @item_add.error
    async def item_add_error(self, i: discord.Interaction, e: app_commands.AppCommandError):
        if isinstance(e, app_commands.MissingPermissions):
            try: await i.response.send_message("Недостаточно прав. Требуются права администратора.", ephemeral=True)
            except: await i.followup.send("Недостаточно прав. Требуются права администратора.", ephemeral=True)

    @app_commands.command(name="del_item", description="🗑️ (Админ) Удалить выбранный предмет из базы данных")
    @app_commands.describe(item="Название предмета для удаления")
    @app_commands.autocomplete(item=_autocomplete_items)
    @app_commands.checks.has_permissions(administrator=True)
    async def del_item(self, interaction: discord.Interaction, item: str):
        await interaction.response.defer(ephemeral=True)
        try:
            if await asyncio.to_thread(self._repo.delete_item, item):
                await asyncio.to_thread(_sync_prices_from_db, self._repo)
                await interaction.followup.send(f"✅ {item} удалён из базы.", ephemeral=True)
            else:
                await interaction.followup.send(embed=error_embed("Предмет не найден."), ephemeral=True)
        except Exception as e:
            await interaction.followup.send(embed=error_embed(f"Ошибка: {e}"), ephemeral=True)

    @del_item.error
    async def del_item_error(self, i: discord.Interaction, e: app_commands.AppCommandError):
        if isinstance(e, app_commands.MissingPermissions):
            try: await i.response.send_message("Недостаточно прав. Требуются права администратора.", ephemeral=True)
            except: await i.followup.send("Недостаточно прав. Требуются права администратора.", ephemeral=True)

    @app_commands.command(name="sync_prices", description="🔄 (Админ) Синхронизировать цены из базы в листы Мейн скуп, Скуп бустов, Продажа бустов")
    @app_commands.checks.has_permissions(administrator=True)
    async def sync_prices(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            updated = await asyncio.to_thread(self._repo.sync_prices_to_sheets)
            await interaction.followup.send(
                f"✅ Синхронизация завершена. Обновлено: ресурсов — {updated.get('resource', 0)}, "
                f"скуп бустов — {updated.get('skup_boost', 0)}, продажа бустов — {updated.get('boost', 0)}.",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.followup.send(embed=error_embed(f"Ошибка: {e}"), ephemeral=True)

    @sync_prices.error
    async def sync_prices_error(self, i: discord.Interaction, e: app_commands.AppCommandError):
        if isinstance(e, app_commands.MissingPermissions):
            try: await i.response.send_message("Недостаточно прав. Требуются права администратора.", ephemeral=True)
            except: await i.followup.send("Недостаточно прав. Требуются права администратора.", ephemeral=True)


def _fmt(n: float) -> str:
    if n == int(n):
        return str(int(n))
    return f"{n:.2f}".rstrip("0").rstrip(".")


def _db_prices_dict(repo: SheetsRepository) -> dict[str, float]:
    items = repo.get_all_items()
    result = {}
    for it in items:
        if it.get("price_buy") is not None:
            result[it["name"]] = it["price_buy"]
        if it.get("price_sell") is not None:
            result[f"{it['name']} (boost)"] = it["price_sell"]
    return result


def _sync_prices_from_db(repo: SheetsRepository) -> dict[str, float]:
    prices = _db_prices_dict(repo)
    with open(PRICES_FILE, "w", encoding="utf-8") as f:
        json.dump(prices, f, ensure_ascii=False, indent=2)
    return prices


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ItemsCog(bot, bot.repo))
