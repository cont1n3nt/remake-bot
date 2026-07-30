import asyncio
import json
import logging
import os
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot.config.constants import (
    PRICE_CHANGE_TITLE_RESOURCE, PRICE_CHANGE_TITLE_BOOST,
    SYNC_SHEET_SKUP, SYNC_SHEET_SKUP_BOOST, SYNC_SHEET_BOOST_SALE,
)
from bot.services.sheets_service import SheetsService
from bot.utils.calculator import safe_calc
from bot.utils.embeds import (
    error_embed, resolve_emoji, format_price_change, normalize_emoji_name,
)

logger = logging.getLogger("bot")

PRICES_FILE = "prices.json"


class ItemsCog(commands.Cog):

    def __init__(self, bot: commands.Bot, sheets_service: SheetsService) -> None:
        self.bot = bot
        self._sheets_service = sheets_service

    @staticmethod
    def _clean_choice_name(it: dict) -> str:
        return it["name"]

    async def _autocomplete_items(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice]:
        items = await asyncio.to_thread(self._sheets_service.get_all_items)
        matches = [it for it in items if current.lower() in it["name"].lower()]
        return [
            app_commands.Choice(
                name=self._clean_choice_name(it),
                value=it["name"],
            )
            for it in matches[:25]
        ]

    async def _autocomplete_resources(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice]:
        items = [it for it in (await asyncio.to_thread(self._sheets_service.get_all_items)) if it["category"] == "resource"]
        matches = [it for it in items if current.lower() in it["name"].lower()]
        return [
            app_commands.Choice(
                name=self._clean_choice_name(it),
                value=it["name"],
            )
            for it in matches[:25]
        ]

    async def _autocomplete_boosts(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice]:
        items = [it for it in (await asyncio.to_thread(self._sheets_service.get_all_items)) if it["category"] == "boost"]
        matches = [it for it in items if current.lower() in it["name"].lower()]
        return [
            app_commands.Choice(
                name=self._clean_choice_name(it),
                value=it["name"],
            )
            for it in matches[:25]
        ]

    async def _send_price_change_embed(self, interaction: discord.Interaction, title: str, changes: list[str]):
        """Единый вид ответа для /setprice, /setboost и /new_price (пункт 19):
        заголовки берутся из констант, чтобы форматы больше не расходились."""
        if not changes:
            return
        embed = discord.Embed(title=title, colour=discord.Colour.blue())
        embed.description = "\n".join(changes)
        await interaction.followup.send(embed=embed, ephemeral=True)

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
            it = await asyncio.to_thread(self._sheets_service.find_item_by_name_and_category, item, "resource")
            if it is None:
                await interaction.followup.send(embed=error_embed("Предмет не найден в категории resource."), ephemeral=True)
                return
            old_price = it.get("price_buy")
            await asyncio.to_thread(self._sheets_service.upsert_item, it["name"], it["category"], price_buy=amount)
            await asyncio.to_thread(_sync_prices_from_db, self._sheets_service)
            change = format_price_change(it, old_price, amount, guild=interaction.guild)
            await self._send_price_change_embed(interaction, PRICE_CHANGE_TITLE_RESOURCE, [change])
            try:
                audit = interaction.client.audit_logger
                await audit.log(interaction.user, "/setprice", [change])
            except Exception:
                pass
        except Exception as e:
            await interaction.followup.send(embed=error_embed(f"Ошибка: {e}"), ephemeral=True)

    @setprice.error
    async def setprice_error(self, i: discord.Interaction, e: app_commands.AppCommandError):
        if isinstance(e, app_commands.MissingPermissions):
            try: await i.response.send_message("Недостаточно прав. Требуются права администратора.", ephemeral=True)
            except: await i.followup.send("Недостаточно прав. Требуются права администратора.", ephemeral=True)

    @app_commands.command(name="setboost", description="🚀 (Админ) Изменить цену продажи буста игрокам")
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
            it = await asyncio.to_thread(self._sheets_service.find_item_by_name_and_category, item, "boost")
            if it is None:
                await interaction.followup.send(embed=error_embed("Буст не найден в категории boost."), ephemeral=True)
                return
            old_price = it.get("price_sell")
            await asyncio.to_thread(self._sheets_service.upsert_item, it["name"], it["category"], price_sell=amount)
            await asyncio.to_thread(_sync_prices_from_db, self._sheets_service)
            change = format_price_change(it, old_price, amount, guild=interaction.guild)
            await self._send_price_change_embed(interaction, PRICE_CHANGE_TITLE_BOOST, [change])
            try:
                audit = interaction.client.audit_logger
                await audit.log(interaction.user, "/setboost", [change])
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
        # В таблицу пишем только имя эмодзи, без ID (пункт 22) — resolve_emoji
        # ниже всё равно развернёт его обратно в <:name:id> для вывода.
        emoji_name = normalize_emoji_name(emoji)
        try:
            it = await asyncio.to_thread(self._sheets_service.upsert_item, name.strip(), category, price_buy=pb, price_sell=ps, emoji=emoji_name)
            await asyncio.to_thread(_sync_prices_from_db, self._sheets_service)
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
    @app_commands.describe(
        item="Название предмета для удаления",
        category="Категория — обязательна, если предмет есть и в ресурсах, и в бустах",
    )
    @app_commands.autocomplete(item=_autocomplete_items)
    @app_commands.choices(category=[
        app_commands.Choice(name="Ресурс (скупка)", value="resource"),
        app_commands.Choice(name="Буст (продажа)", value="boost"),
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def del_item(self, interaction: discord.Interaction, item: str, category: Optional[str] = None):
        await interaction.response.defer(ephemeral=True)
        try:
            if await asyncio.to_thread(self._sheets_service.delete_item, item, category):
                await asyncio.to_thread(_sync_prices_from_db, self._sheets_service)
                await interaction.followup.send(f"✅ {item} удалён из базы.", ephemeral=True)
            else:
                # delete_item отказывается удалять при неоднозначности, чтобы не
                # снести одноимённый предмет из другой категории.
                await interaction.followup.send(
                    embed=error_embed(
                        f"«{item}» не найден или существует сразу в двух категориях. "
                        "Укажите категорию параметром `category`."
                    ),
                    ephemeral=True,
                )
        except Exception as e:
            await interaction.followup.send(embed=error_embed(f"Ошибка: {e}"), ephemeral=True)

    @del_item.error
    async def del_item_error(self, i: discord.Interaction, e: app_commands.AppCommandError):
        if isinstance(e, app_commands.MissingPermissions):
            try: await i.response.send_message("Недостаточно прав. Требуются права администратора.", ephemeral=True)
            except: await i.followup.send("Недостаточно прав. Требуются права администратора.", ephemeral=True)

    @app_commands.command(name="sync_prices", description="🔄 (Админ) Синхронизировать цены из базы в листы Мейн скуп, Скуп бустов, БУСТЫ")
    @app_commands.checks.has_permissions(administrator=True)
    async def sync_prices(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            result = await asyncio.to_thread(self._sheets_service.sync_prices_to_sheets)
            not_found = result.get("not_found", [])
            errors = result.get("errors", [])
            protected = result.get("protected", [])

            embed = discord.Embed(
                title="🔄 Синхронизация цен",
                colour=discord.Colour.red() if errors else discord.Colour.green(),
            )
            embed.add_field(
                name="📄 Обновлено",
                value=(
                    f"• {SYNC_SHEET_SKUP}: {result.get('resource', 0)}\n"
                    f"• {SYNC_SHEET_SKUP_BOOST}: {result.get('skup_boost', 0)}\n"
                    f"• {SYNC_SHEET_BOOST_SALE}: {result.get('boost', 0)}"
                ),
                inline=False,
            )
            if not_found:
                text = "\n".join(f"• {n}" for n in not_found[:20])
                if len(not_found) > 20:
                    text += f"\n… и ещё {len(not_found) - 20}"
                embed.add_field(name=f"⚠️ Не найдены в листах ({len(not_found)})", value=text[:1024], inline=False)
            if errors:
                text = "\n".join(f"• {e}" for e in errors[:10])
                if len(errors) > 10:
                    text += f"\n… и ещё {len(errors) - 10}"
                embed.add_field(name=f"❌ Ошибки при записи ({len(errors)})", value=text[:1024], inline=False)
            if protected:
                # Защиту диапазона обойти нельзя — это право владельца таблицы.
                account = result.get("service_account") or "сервис-аккаунт из credentials.json"
                embed.add_field(
                    name="🔒 Что сделать",
                    value=(
                        f"Листы {', '.join(sorted(set(protected)))} защищены от редактирования.\n"
                        f"Откройте в таблице Данные → Защищённые листы и диапазоны и добавьте "
                        f"в список редакторов:\n{account}"
                    )[:1024],
                    inline=False,
                )
            await interaction.followup.send(embed=embed, ephemeral=True)
            try:
                audit = interaction.client.audit_logger
                audit_lines = [
                    f"{SYNC_SHEET_SKUP}: {result.get('resource', 0)} обновлено",
                    f"{SYNC_SHEET_SKUP_BOOST}: {result.get('skup_boost', 0)} обновлено",
                    f"{SYNC_SHEET_BOOST_SALE}: {result.get('boost', 0)} обновлено",
                ]
                if not_found:
                    audit_lines.append(f"Не найдено: {len(not_found)}")
                if errors:
                    audit_lines.append(f"Ошибки: {len(errors)}")
                if protected:
                    audit_lines.append(f"Защищены: {', '.join(sorted(set(protected)))}")
                await audit.log(interaction.user, "/sync_prices", audit_lines, success=not errors)
            except Exception:
                pass
        except Exception as e:
            await interaction.followup.send(embed=error_embed(f"Ошибка: {e}"), ephemeral=True)

    @sync_prices.error
    async def sync_prices_error(self, i: discord.Interaction, e: app_commands.AppCommandError):
        if isinstance(e, app_commands.MissingPermissions):
            try: await i.response.send_message("Недостаточно прав. Требуются права администратора.", ephemeral=True)
            except: await i.followup.send("Недостаточно прав. Требуются права администратора.", ephemeral=True)


def _db_prices_dict(sheets_service: SheetsService) -> dict[str, float]:
    items = sheets_service.get_all_items()
    result = {}
    for it in items:
        if it.get("price_buy") is not None:
            result[it["name"]] = it["price_buy"]
        if it.get("price_sell") is not None:
            result[f"{it['name']} (boost)"] = it["price_sell"]
    return result


def _sync_prices_from_db(sheets_service: SheetsService) -> dict[str, float]:
    prices = _db_prices_dict(sheets_service)
    with open(PRICES_FILE, "w", encoding="utf-8") as f:
        json.dump(prices, f, ensure_ascii=False, indent=2)
    return prices


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ItemsCog(bot, bot.sheets_service))
