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
    s = str(val).strip().replace(" ", "").replace(",", ".").replace("₽", "")
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
                is_sell = len(row) > 3 and str(row[3]).strip().upper() == "TRUE"
                amount = _parse_float(row[4]) if len(row) > 4 else 0.0
                if amount == 0.0 and not is_buy and not is_sell:
                    continue
                referrer = str(row[7]).strip() if len(row) > 7 and row[7] else ""
                txs.append({
                    "date": raw_date,
                    "nickname": nickname,
                    "is_buy": is_buy,
                    "is_sell": is_sell,
                    "amount": amount,
                    "referrer": referrer,
                })
            except (ValueError, IndexError):
                continue
        return txs

    def _build_analytics_embed(self, txs: list[dict], title: str) -> discord.Embed:
        """Строит embed аналитики.
        
        is_buy = колонка C (Покупка) — игрок продаёт боту (бот покупает)
        is_sell = колонка D (Продажа) — игрок покупает у бота (бот продаёт)

        С точки зрения игрока:
          - Продажа: игрок продаёт → is_buy = TRUE → player_sell
          - Покупка: игрок покупает → is_sell = TRUE → player_buy
        """
        players = {}
        total_player_sell = 0.0  # игроки продали боту
        total_player_buy = 0.0   # игроки купили у бота
        for tx in txs:
            nick = tx["nickname"]
            if nick not in players:
                players[nick] = {"sell": 0.0, "buy": 0.0}
            if tx["is_buy"]:
                players[nick]["sell"] += tx["amount"]
                total_player_sell += tx["amount"]
            if tx["is_sell"]:
                players[nick]["buy"] += tx["amount"]
                total_player_buy += tx["amount"]

        embed = discord.Embed(title=title, colour=discord.Colour.blue())
        lines = []
        for nick, data in sorted(players.items(), key=lambda x: x[1]["sell"] + x[1]["buy"], reverse=True):
            total = data["sell"] + data["buy"]
            parts = [f"• {nick}"]
            if data["sell"] > 0:
                parts.append(f"Продажа {_fmt(data['sell'])}₽")
            if data["buy"] > 0:
                parts.append(f"Покупка {_fmt(data['buy'])}₽")
            parts.append(f"Оборот {_fmt(total)}₽")
            lines.append(" | ".join(parts))

        if lines:
            embed.description = "```\n" + "\n".join(lines[:30]) + "```"
            if len(lines) > 30:
                embed.set_footer(text=f"… и ещё {len(lines) - 30} игроков")
        else:
            embed.description = "Нет сделок за выбранный период."

        # Прибыль бота = продажи бота (игрок купил) - покупки бота (игрок продал)
        bot_profit = total_player_buy - total_player_sell

        embed.add_field(name="💰 Сумма продаж игроков", value=f"{_fmt(total_player_sell)} ₽")
        embed.add_field(name="🛒 Сумма покупок игроков", value=f"{_fmt(total_player_buy)} ₽")
        embed.add_field(
            name="📊 Прибыль бота",
            value=f"{_fmt(bot_profit)} ₽" if bot_profit >= 0 else f"-{_fmt(abs(bot_profit))} ₽",
        )
        embed.add_field(name="📝 Количество сделок", value=str(len(txs)), inline=False)
        return embed

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

    @app_commands.command(name="week", description="📈 (Админ) Показать статистику продаж за выбранный период")
    @app_commands.describe(диапазон="Диапазон дат: ДД.ММ.ГГГГ - ДД.ММ.ГГГГ (например 20.07.2026 - 26.07.2026)")
    @app_commands.checks.has_permissions(administrator=True)
    async def week(self, interaction: discord.Interaction, диапазон: str):
        await interaction.response.defer(ephemeral=True)
        try:
            parts = [p.strip() for p in диапазон.split("-")]
            if len(parts) != 2:
                raise ValueError
            start = datetime.strptime(parts[0], "%d.%m.%Y").date()
            end = datetime.strptime(parts[1], "%d.%m.%Y").date()
        except (ValueError, IndexError):
            await interaction.followup.send(
                embed=error_embed("Неверный формат. Используйте: ДД.ММ.ГГГГ - ДД.ММ.ГГГГ (например 20.07.2026 - 26.07.2026)"),
                ephemeral=True)
            return
        try:
            txs = await asyncio.to_thread(self._get_transactions_for_period, start, end)
            embed = self._build_analytics_embed(
                txs,
                f"📈 Статистика за {start.strftime('%d.%m.%Y')} - {end.strftime('%d.%m.%Y')}",
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(embed=error_embed(f"Ошибка: {e}"), ephemeral=True)

    @week.error
    async def week_error(self, i: discord.Interaction, e: app_commands.AppCommandError):
        if isinstance(e, app_commands.MissingPermissions):
            try: await i.response.send_message("Недостаточно прав. Требуются права администратора.", ephemeral=True)
            except: await i.followup.send("Недостаточно прав. Требуются права администратора.", ephemeral=True)

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
