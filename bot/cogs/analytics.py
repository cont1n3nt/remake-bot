import asyncio
import calendar
import logging
from datetime import date, datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands

from bot.repositories.sheets_repository import SheetsRepository
from bot.utils.embeds import error_embed

logger = logging.getLogger("bot")

DATA_START_ROW = 3


def _fmt(n: float) -> str:
    if n == int(n):
        return str(int(n))
    return f"{n:.2f}".rstrip("0").rstrip(".")


def _parse_float(val) -> float:
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


class AnalyticsCog(commands.Cog):

    def __init__(self, bot: commands.Bot, repo: SheetsRepository) -> None:
        self.bot = bot
        self._repo = repo

    def _get_transactions_for_period(self, start: date, end: date) -> list[dict]:
        vals = self._repo.get_transactions()
        txs = []
        seen_rows = set()
        for row in vals:
            if len(row) < 5:
                continue
            row_key = tuple(row)
            if row_key in seen_rows:
                continue
            seen_rows.add(row_key)
            try:
                raw_date = str(row[0]).strip() if row[0] else ""
                if not raw_date:
                    continue
                tx_date = None
                for fmt in ("%d.%m.%y %H:%M", "%d.%m.%Y %H:%M", "%d.%m.%y", "%d.%m.%Y"):
                    try:
                        tx_date = datetime.strptime(raw_date, fmt).date()
                        break
                    except ValueError:
                        continue
                if tx_date is None:
                    continue
                if not (start <= tx_date <= end):
                    continue
                nickname = str(row[1]).strip() if len(row) > 1 else ""
                is_buy = len(row) > 2 and str(row[2]).strip().upper() == "TRUE"
                amount = _parse_float(row[4]) if len(row) > 4 else 0.0
                referrer = str(row[7]).strip() if len(row) > 7 and row[7] else ""
                txs.append({
                    "date": raw_date,
                    "nickname": nickname,
                    "type": "buy" if is_buy else "sell",
                    "amount": amount,
                    "referrer": referrer,
                })
            except (ValueError, IndexError):
                continue
        return txs

    def _build_analytics_embed(self, txs: list[dict], title: str) -> discord.Embed:
        players = {}
        total_buy = 0.0
        total_sell = 0.0
        for tx in txs:
            nick = tx["nickname"]
            if nick not in players:
                players[nick] = {"buy": 0.0, "sell": 0.0}
            if tx["type"] == "buy":
                players[nick]["buy"] += tx["amount"]
                total_buy += tx["amount"]
            else:
                players[nick]["sell"] += tx["amount"]
                total_sell += tx["amount"]
        embed = discord.Embed(title=title, colour=discord.Colour.blue())
        lines = []
        for nick, data in sorted(players.items(), key=lambda x: x[1]["buy"] + x[1]["sell"], reverse=True):
            total = data["buy"] + data["sell"]
            lines.append(
                f"• {nick}: Покупка {_fmt(data['buy'])}₽ | "
                f"Продажа {_fmt(data['sell'])}₽ | "
                f"Оборот {_fmt(total)}₽"
            )
        if lines:
            embed.description = "```\n" + "\n".join(lines[:30]) + "```"
            if len(lines) > 30:
                embed.set_footer(text=f"… и ещё {len(lines) - 30} игроков")
        else:
            embed.description = "Нет сделок за выбранный период."
        profit = total_buy - total_sell
        embed.add_field(name="Общий оборот продаж", value=f"{_fmt(total_buy)} ₽")
        embed.add_field(name="Общий оборот покупок", value=f"{_fmt(total_sell)} ₽")
        embed.add_field(
            name="Чистая прибыль",
            value=f"{_fmt(profit)} ₽" if profit >= 0 else f"-{_fmt(abs(profit))} ₽",
        )
        return embed

    # ------------------------------------------------------------------
    #  /day
    # ------------------------------------------------------------------
    @app_commands.command(name="day", description="📊 (Админ) Показать аналитику и статистику продаж за конкретный день")
    @app_commands.describe(дата="Дата в формате ДД.ММ.ГГГГ (например 26.07.2026)")
    @app_commands.checks.has_permissions(administrator=True)
    async def day(self, interaction: discord.Interaction, дата: str):
        await interaction.response.defer(ephemeral=True)
        try:
            d = datetime.strptime(дата.strip(), "%d.%m.%Y").date()
        except ValueError:
            await interaction.followup.send(
                embed=error_embed("Неверный формат даты. Используйте ДД.ММ.ГГГГ"), ephemeral=True)
            return
        try:
            txs = await asyncio.to_thread(self._get_transactions_for_period, d, d)
            embed = self._build_analytics_embed(txs, f"📊 Статистика за {d.strftime('%d.%m.%Y')}")
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(embed=error_embed(f"Ошибка: {e}"), ephemeral=True)

    @day.error
    async def day_error(self, i: discord.Interaction, e: app_commands.AppCommandError):
        if isinstance(e, app_commands.MissingPermissions):
            try: await i.response.send_message("Недостаточно прав. Требуются права администратора.", ephemeral=True)
            except: await i.followup.send("Недостаточно прав. Требуются права администратора.", ephemeral=True)

    # ------------------------------------------------------------------
    #  /week
    # ------------------------------------------------------------------
    @app_commands.command(name="week", description="📈 (Админ) Показать статистику продаж за выбранную неделю")
    @app_commands.describe(год="Год (например 2026)", неделя="Номер недели (1-53)")
    @app_commands.checks.has_permissions(administrator=True)
    async def week(self, interaction: discord.Interaction, год: int, неделя: int):
        await interaction.response.defer(ephemeral=True)
        try:
            start = datetime.strptime(f"{год}-W{неделя:02d}-1", "%G-W%V-%u").date()
            end = start + timedelta(days=6)
        except ValueError:
            await interaction.followup.send(embed=error_embed("Неверный номер недели."), ephemeral=True)
            return
        try:
            txs = await asyncio.to_thread(self._get_transactions_for_period, start, end)
            embed = self._build_analytics_embed(
                txs,
                f"📈 Статистика за {неделя}-ю неделю {год} ({start.strftime('%d.%m')}-{end.strftime('%d.%m')})",
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(embed=error_embed(f"Ошибка: {e}"), ephemeral=True)

    @week.error
    async def week_error(self, i: discord.Interaction, e: app_commands.AppCommandError):
        if isinstance(e, app_commands.MissingPermissions):
            try: await i.response.send_message("Недостаточно прав. Требуются права администратора.", ephemeral=True)
            except: await i.followup.send("Недостаточно прав. Требуются права администратора.", ephemeral=True)

    # ------------------------------------------------------------------
    #  /month
    # ------------------------------------------------------------------
    @app_commands.command(name="month", description="📉 (Админ) Показать аналитику и статистику продаж за полный месяц")
    @app_commands.describe(месяц="Месяц (1-12)", год="Год (например 2026)")
    @app_commands.checks.has_permissions(administrator=True)
    async def month(self, interaction: discord.Interaction, месяц: int, год: int):
        await interaction.response.defer(ephemeral=True)
        if not 1 <= месяц <= 12:
            await interaction.followup.send(embed=error_embed("Месяц должен быть от 1 до 12."), ephemeral=True)
            return
        try:
            start = date(год, месяц, 1)
            end = date(год, месяц, calendar.monthrange(год, месяц)[1])
            txs = await asyncio.to_thread(self._get_transactions_for_period, start, end)
            embed = self._build_analytics_embed(
                txs,
                f"📉 Статистика за {start.strftime('%B %Y')} ({start.strftime('%d.%m')}-{end.strftime('%d.%m')})",
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(embed=error_embed(f"Ошибка: {e}"), ephemeral=True)

    @month.error
    async def month_error(self, i: discord.Interaction, e: app_commands.AppCommandError):
        if isinstance(e, app_commands.MissingPermissions):
            try: await i.response.send_message("Недостаточно прав. Требуются права администратора.", ephemeral=True)
            except: await i.followup.send("Недостаточно прав. Требуются права администратора.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AnalyticsCog(bot, bot.repo))
