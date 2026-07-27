"""Discord-бот для маркетплейса «Клондайк Шёпота».
Автоматизация тикетов, OCR скриншотов, управление ценами и товарами,
статистика продаж, выдача ролей, интеграция с Google Таблицами."""

import asyncio
import csv
import io
import json
import logging
import math
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

import discord
from discord import Object, app_commands
from discord.ext import commands

logger = logging.getLogger("bot")

# =================================================================== #
#  (b)  КОНСТАНТЫ                                                     #
# =================================================================== #

BOT_TOKEN = os.getenv("DISCORD_TOKEN", "")
GOOGLE_SHEETS_CREDS = os.getenv("GOOGLE_SHEETS_CREDS", "credentials.json")
GOOGLE_SHEETS_URL = os.getenv("GOOGLE_SHEETS_URL", "")
GUILD_ID = int(os.getenv("GUILD_ID", "0"))
SHEET_NAME = os.getenv("SHEET_NAME", "Лист1")
AUDIT_CHANNEL_ID = int(os.getenv("AUDIT_CHANNEL_ID", "0"))

# --- Отслеживаемые каналы ---
MONITORED_CHANNELS: list[int] = [
    1437410969704730746,
    1430173956492755105,
    1428822870003290112,
    1503656186535350334,
    1283776718435516469,
    1510608354584821931,
    1509663894686269561,
]

# --- Тикет-каналы ---
CATEGORY_CHANNELS: dict[str, int] = {
    "Продажа предметов": 1475149130748657841,
    "Продажа бустов":   1503802805801058336,
    "Заказ бустов":     1479228622014251049,
}

CATEGORY_FIELDS: dict[str, list[dict]] = {
    "Продажа предметов": [
        {"label": "Название предмета", "custom_id": "item_name",  "required": True,  "style": discord.TextStyle.short},
        {"label": "Количество",        "custom_id": "quantity",   "required": True,  "style": discord.TextStyle.short},
        {"label": "Цена за единицу",   "custom_id": "unit_price", "required": True,  "style": discord.TextStyle.short},
        {"label": "Описание",          "custom_id": "description","required": False, "style": discord.TextStyle.paragraph},
    ],
    "Продажа бустов": [
        {"label": "Название буста", "custom_id": "boost_name", "required": True,  "style": discord.TextStyle.short},
        {"label": "Стоимость",      "custom_id": "cost",       "required": True,  "style": discord.TextStyle.short},
        {"label": "Описание",       "custom_id": "description","required": False, "style": discord.TextStyle.paragraph},
    ],
    "Заказ бустов": [
        {"label": "Название буста", "custom_id": "boost_name", "required": True,  "style": discord.TextStyle.short},
        {"label": "Сервис",         "custom_id": "service",    "required": True,  "style": discord.TextStyle.short},
        {"label": "Бюджет",         "custom_id": "budget",     "required": True,  "style": discord.TextStyle.short},
        {"label": "Описание",       "custom_id": "description","required": False, "style": discord.TextStyle.paragraph},
    ],
}

# --- Ранговые роли ---
RANK_THRESHOLDS: dict[str, int] = {
    "Standard": 0, "Premium": 5000, "Prestige": 25000, "Elite": 100000, "Legend": 500000,
}
RANK_ROLES: dict[str, int] = {
    "Standard": 1518324856549277827, "Premium": 1518328036137631805,
    "Prestige": 1518328037631066232, "Elite": 1518328222939611166,
    "Legend": 1518328324605083698,
}

# --- Реферальные роли ---
REFERRAL_THRESHOLDS: dict[str, int] = {
    "Скаут": 1, "Промоутер": 5, "Вербовщик": 10, "Амбассадор": 25, "Рекламный Барон": 100,
}
REFERRAL_ROLES: dict[str, int] = {
    "Скаут": 1518583879672270878, "Промоутер": 1518584176054636584,
    "Вербовщик": 1518584268933300274, "Амбассадор": 1518584424818671687,
    "Рекламный Барон": 1518584494410563625,
}

# --- Google Sheets: основная таблица сделок (B:H) ---
COL_NICKNAME = 2; COL_BUY = 3; COL_SELL = 4; COL_AMOUNT = 5
COL_REFERRED_BY = 8; DATA_START_ROW = 3

# --- Google Sheets: база пользователей (I:J) ---
COL_DISCORD_ID = 9; COL_UNIQUE_NICK = 10; COL_TOTAL_COINS = 11; COL_TOTAL_XP = 12
COL_TOTAL_TURNOVER = 15; COL_REFERRAL_COUNT = 16; COL_BOOSTER = 17; COL_RANK = 18; COL_REFERRAL_ROLE = 19

# --- Google Sheets: база предметов DataBase (AA:AG) ---
COL_DB_ID = 27; COL_DB_NAME = 28; COL_DB_CATEGORY = 29
COL_DB_PRICE_BUY = 30; COL_DB_PRICE_SELL = 31; COL_DB_EMOJI = 32; COL_DB_UPDATED = 33

# --- Пути ---
PRICES_FILE = "prices.json"; REFERRALS_FILE = "referrals.json"; DEAL_REPORTS_DIR = "deal_reports"

# =================================================================== #
#  (c)  ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ                                       #
# =================================================================== #

import re as _re2

_EMOJI_RE = _re2.compile(r"<a?:\w+:\d+>")

def _resolve_emoji(emoji_str: str, guild=None) -> str:
    if not emoji_str:
        return ""
    if _EMOJI_RE.match(emoji_str):
        return emoji_str
    if guild is None:
        return ""
    name = emoji_str.strip(":")
    for e in guild.emojis:
        if e.name == name:
            return str(e)
    return ""

def _fmt(n: float) -> str:
    if n == int(n): return str(int(n))
    return f"{n:.2f}".rstrip("0").rstrip(".")

def is_monitored(channel_id: int) -> bool:
    return channel_id in MONITORED_CHANNELS

async def _safe_defer(interaction: discord.Interaction, ephemeral: bool = True) -> None:
    if not interaction.response.is_done():
        try:
            await interaction.response.defer(ephemeral=ephemeral)
        except Exception:
            pass

# ------------------------------------------------------------------ #
#  SAFE CALCULATOR                                                   #
# ------------------------------------------------------------------ #

def safe_calc(expression: str) -> float:
    import re as _re
    if not expression or not expression.strip():
        raise ValueError("Сумма не может быть пустой")
    cleaned = _re.sub(r'[^\d.,+*/\skк-]', '', expression)
    cleaned = _re.sub(r'\s', '', cleaned)
    cleaned = cleaned.replace(",", ".")
    cleaned = _re.sub(r'(\d+)[kк]', r'\1*1000', cleaned, flags=_re.IGNORECASE)
    if not cleaned:
        raise ValueError("Сумма не может быть пустой")
    import math
    import simpleeval
    result = simpleeval.simple_eval(
        cleaned,
        functions={"int": int, "float": float, "abs": abs, "round": round},
        names={},
    )
    if not isinstance(result, (int, float)):
        raise ValueError("Результат не является числом")
    value = float(result)
    if not math.isfinite(value):
        raise ValueError("Сумма должна быть конечным числом")
    return value

# ------------------------------------------------------------------ #
#  GOOGLE SHEETS – подключение                                        #
# ------------------------------------------------------------------ #

_gs_client = None

def _gs():
    global _gs_client
    if _gs_client is not None:
        return _gs_client
    import gspread
    from google.oauth2.service_account import Credentials
    creds = Credentials.from_service_account_file(
        GOOGLE_SHEETS_CREDS,
        scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"],
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_url(GOOGLE_SHEETS_URL)
    _gs_client = sh.worksheet(SHEET_NAME)
    return _gs_client

def _gs_parse_float(val) -> float:
    if val is None: return 0.0
    if isinstance(val, (int, float)): return float(val)
    s = str(val).strip().replace(" ", "").replace(",", ".")
    try: return float(s)
    except ValueError: return 0.0

def _gs_parse_int(val) -> int:
    if val is None: return 0
    if isinstance(val, (int, float)): return int(val)
    s = str(val).strip().replace(" ", "").replace(",", ".")
    try: return int(float(s))
    except ValueError: return 0

# ------------------------------------------------------------------ #
#  ПОЛЬЗОВАТЕЛИ  (Google Sheets – столбцы I:J)                       #
# ------------------------------------------------------------------ #

@dataclass
class User:
    nickname: str; coins: float = 0.0; xp: float = 0.0
    rank: str = ""; referral_count: int = 0; referral_role: str = ""
    referred_by: Optional[str] = None; booster: bool = False; turnover: float = 0.0
    discord_id: Optional[int] = None

def _gs_find_user(nickname: str) -> Optional[dict]:
    try: ws = _gs()
    except Exception: return None
    cell = ws.find(nickname, in_column=COL_UNIQUE_NICK)
    if cell is None: return None
    vals = ws.row_values(cell.row)
    referred_by = None
    for tc in ws.findall(nickname, in_column=COL_NICKNAME) or []:
        h = ws.cell(tc.row, COL_REFERRED_BY).value
        if h and h.strip(): referred_by = h.strip(); break
    did = _gs_parse_int(vals[COL_DISCORD_ID - 1]) if len(vals) >= COL_DISCORD_ID else None
    return {
        "nickname": vals[COL_UNIQUE_NICK - 1] if len(vals) >= COL_UNIQUE_NICK else nickname,
        "coins": _gs_parse_float(vals[COL_TOTAL_COINS - 1]) if len(vals) >= COL_TOTAL_COINS else 0.0,
        "xp": _gs_parse_float(vals[COL_TOTAL_XP - 1]) if len(vals) >= COL_TOTAL_XP else 0.0,
        "rank": vals[COL_RANK - 1] if len(vals) >= COL_RANK else "",
        "referral_count": _gs_parse_int(vals[COL_REFERRAL_COUNT - 1]) if len(vals) >= COL_REFERRAL_COUNT else 0,
        "referral_role": vals[COL_REFERRAL_ROLE - 1] if len(vals) >= COL_REFERRAL_ROLE else "",
        "booster": len(vals) >= COL_BOOSTER and vals[COL_BOOSTER - 1] == "TRUE",
        "referred_by": referred_by,
        "turnover": _gs_parse_float(ws.cell(cell.row, COL_TOTAL_TURNOVER).value),
        "discord_id": did,
    }

def _gs_get_user_profile(nickname: str) -> Optional[User]:
    d = _gs_find_user(nickname)
    if d is None: return None
    return User(**{k: v for k, v in d.items() if k in User.__dataclass_fields__})

def _gs_get_referred_users(nickname: str) -> list[dict]:
    try: ws = _gs()
    except Exception: return []
    result = []
    for c in ws.findall(nickname, in_column=COL_REFERRED_BY) or []:
        nick = ws.cell(c.row, COL_NICKNAME).value
        if nick: result.append({"nickname": nick.strip(), "date": "", "row": c.row})
    return result

def _gs_set_referred_by(nickname: str, referrer: str) -> None:
    ws = _gs()
    for c in ws.findall(nickname, in_column=COL_NICKNAME) or []:
        ws.update_cell(c.row, COL_REFERRED_BY, referrer)

def _gs_copy_formulas(ws, index: int) -> None:
    """Copy formulas from K-U range of the row above into the new row."""
    if index <= 3:
        return
    try:
        sid = ws.id
        ws.client.session.post(
            f"https://sheets.googleapis.com/v4/spreadsheets/{ws.spreadsheet.id}:batchUpdate",
            json={
                "requests": [{
                    "copyPaste": {
                        "source": {
                            "sheetId": sid,
                            "startRowIndex": index - 2,
                            "endRowIndex": index - 1,
                            "startColumnIndex": 10,
                            "endColumnIndex": 21,
                        },
                        "destination": {
                            "sheetId": sid,
                            "startRowIndex": index - 1,
                            "endRowIndex": index,
                            "startColumnIndex": 10,
                            "endColumnIndex": 21,
                        },
                        "pasteType": "PASTE_FORMULA",
                    }
                }]
            },
        )
    except Exception:
        pass


def _gs_ensure_jrow(nickname: str) -> None:
    """Copy K-U formulas to the J-row for this nickname if empty."""
    try:
        ws = _gs()
        cell = ws.find(nickname, in_column=COL_UNIQUE_NICK)
        if cell is None:
            return
        k_val = ws.cell(cell.row, COL_TOTAL_COINS).value
        if k_val and str(k_val).strip():
            return
        src = None
        for bcell in ws.findall(nickname, in_column=COL_NICKNAME):
            br = ws.row_values(bcell.row)
            if len(br) >= COL_TOTAL_COINS and br[COL_TOTAL_COINS - 1].strip():
                src = bcell.row
                break
        if src is None or src == cell.row:
            return
        sid = ws.id
        ws.client.session.post(
            f"https://sheets.googleapis.com/v4/spreadsheets/{ws.spreadsheet.id}:batchUpdate",
            json={
                "requests": [{
                    "copyPaste": {
                        "source": {
                            "sheetId": sid,
                            "startRowIndex": src - 1,
                            "endRowIndex": src,
                            "startColumnIndex": 10,
                            "endColumnIndex": 21,
                        },
                        "destination": {
                            "sheetId": sid,
                            "startRowIndex": cell.row - 1,
                            "endRowIndex": cell.row,
                            "startColumnIndex": 10,
                            "endColumnIndex": 21,
                        },
                        "pasteType": "PASTE_FORMULA",
                    }
                }]
            },
        )
    except Exception:
        pass


def _gs_add_transaction(nickname: str, tx_type: str, amount: float, referrer: Optional[str] = None) -> None:
    ws = _gs()
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc) + timedelta(hours=3)
    date_str = now.strftime("%d.%m.%y %H:%M")
    last = len(ws.col_values(COL_NICKNAME)) + 1
    row = [date_str, nickname, tx_type == "buy", tx_type == "sell", amount, "", "", referrer or ""]
    ws.insert_row(row, max(last, DATA_START_ROW))
    _gs_copy_formulas(ws, max(last, DATA_START_ROW))
    _gs_ensure_jrow(nickname)
    if referrer:
        _gs_ensure_jrow(referrer)

# ------------------------------------------------------------------ #
#  БАЗА ПРЕДМЕТОВ  (Google Sheets – столбцы AA:AG)                   #
# ------------------------------------------------------------------ #

@dataclass
class Item:
    id: int; name: str; category: str
    price_buy: Optional[float] = None; price_sell: Optional[float] = None
    emoji: str = ""; updated_at: str = ""

def _db_get_all() -> list[Item]:
    try: ws = _gs()
    except Exception: return []
    vals = ws.get(f"AA{ DATA_START_ROW }:AG{ len(ws.col_values(COL_DB_ID)) + 1 }")
    items = []
    for row in vals:
        if len(row) < 2 or not str(row[1]).strip(): continue
        try:
            items.append(Item(
                id=int(row[0]) if row[0] else 0,
                name=str(row[1]).strip(),
                category=str(row[2]).strip() if len(row) > 2 else "",
                price_buy=_gs_parse_float(row[3]) if len(row) > 3 else None,
                price_sell=_gs_parse_float(row[4]) if len(row) > 4 else None,
                emoji=str(row[5]).strip() if len(row) > 5 else "",
                updated_at=str(row[6]).strip() if len(row) > 6 else "",
            ))
        except (ValueError, IndexError):
            continue
    return items

def _db_get_items() -> dict[str, Item]:
    return {it.name.lower(): it for it in _db_get_all()}

def _db_next_id() -> int:
    items = _db_get_all()
    return max((it.id for it in items), default=0) + 1

def _db_upsert(name: str, category: str, price_buy: Optional[float] = None,
              price_sell: Optional[float] = None, emoji: str = "") -> Item:
    ws = _gs()
    now = (datetime.now(timezone_utc()) + timedelta(hours=3)).strftime("%d.%m.%Y %H:%M")
    all_items = _db_get_all()
    existing = next((it for it in all_items if it.name.lower() == name.lower()), None)
    if existing:
        row_num = DATA_START_ROW + all_items.index(existing)
        updates = {}
        if category: updates[COL_DB_CATEGORY] = category
        if price_buy is not None: updates[COL_DB_PRICE_BUY] = price_buy
        if price_sell is not None: updates[COL_DB_PRICE_SELL] = price_sell
        if emoji: updates[COL_DB_EMOJI] = emoji
        updates[COL_DB_UPDATED] = now
        for col, val in updates.items():
            ws.update_cell(row_num, col, val)
        existing.category = category or existing.category
        existing.price_buy = price_buy if price_buy is not None else existing.price_buy
        existing.price_sell = price_sell if price_sell is not None else existing.price_sell
        existing.emoji = emoji or existing.emoji; existing.updated_at = now
        return existing
    new_id = _db_next_id()
    row = [new_id, name, category,
           price_buy if price_buy is not None else "",
           price_sell if price_sell is not None else "",
           emoji, now]
    ws.append_row(row)
    return Item(id=new_id, name=name, category=category,
                price_buy=price_buy, price_sell=price_sell, emoji=emoji, updated_at=now)

def _db_delete(name: str) -> bool:
    ws = _gs()
    cell = ws.find(name, in_column=COL_DB_NAME)
    if cell is None: return False
    ws.delete_rows(cell.row)
    return True

def _db_get_price(item_name: str, category: str) -> Optional[float]:
    items = _db_get_items()
    it = items.get(item_name.lower())
    if it is None: return None
    return it.price_buy if category == "resource" else it.price_sell

def _db_emoji(item_name: str) -> str:
    items = _db_get_items()
    it = items.get(item_name.lower())
    return it.emoji if it else ""

def _db_prices_dict() -> dict[str, float]:
    items = _db_get_all()
    result = {}
    for it in items:
        if it.price_buy is not None:
            result[it.name] = it.price_buy
        if it.price_sell is not None:
            result[f"{it.name} (boost)"] = it.price_sell
    return result

def timezone_utc():
    return datetime.now().astimezone().tzinfo or timedelta(hours=0)

# ------------------------------------------------------------------ #
#  PRICES.JSON  (локальный кеш для OCR)                               #
# ------------------------------------------------------------------ #

def _load_prices() -> dict[str, float]:
    if not os.path.exists(PRICES_FILE): return {}
    try:
        with open(PRICES_FILE, encoding="utf-8") as f:
            return json.load(f)
    except: return {}

def _save_prices(prices: dict[str, float]) -> None:
    with open(PRICES_FILE, "w", encoding="utf-8") as f:
        json.dump(prices, f, ensure_ascii=False, indent=2)

def _sync_prices_from_db() -> dict[str, float]:
    prices = _db_prices_dict()
    _save_prices(prices)
    return prices

# ------------------------------------------------------------------ #
#  ЭМБЕДЫ                                                            #
# ------------------------------------------------------------------ #

def _error_embed(msg: str) -> discord.Embed:
    return discord.Embed(title="Ошибка", description=msg, colour=discord.Colour.red())

def _profile_embed(user: User, rank_progress=None, rank_bonus: str = "") -> discord.Embed:
    embed = discord.Embed(title=f"Профиль — {user.nickname}", colour=discord.Colour.blurple())
    embed.add_field(name="\U0001fa99 Coins", value=_fmt(user.coins))
    embed.add_field(name="\u26a1 XP", value=_fmt(user.xp))
    if user.turnover: embed.add_field(name="Общий оборот", value=_fmt(user.turnover))
    if user.rank: embed.add_field(name="Ранг", value=user.rank)
    if user.referral_role: embed.add_field(name="Реферальная роль", value=user.referral_role)
    if user.referral_count: embed.add_field(name="Приглашено", value=_fmt(user.referral_count))
    if rank_bonus: embed.add_field(name="Бонус текущего ранга", value=rank_bonus, inline=False)
    if rank_progress:
        cur, need, nxt = rank_progress
        bar = "🟦" * min(int(cur / need * 10) if need else 0, 10) + "⬜" * (10 - min(int(cur / need * 10) if need else 0, 10))
        embed.add_field(name=f"До «{nxt}»", value=f"{bar} {_fmt(cur)} / {_fmt(need)} XP", inline=False)
    if user.booster: embed.add_field(name="\U0001f680 Бустер сервера", value="✅")
    if user.referred_by: embed.add_field(name="Ник пригласившего", value=user.referred_by)
    return embed

def _referral_embed(referrer: str) -> discord.Embed:
    return discord.Embed(title="Реферал указан", description=f"Вы указали, что вас пригласил: `{referrer}`", colour=discord.Colour.blurple())

def _referrals_embed(user: User, referred: list[dict], level: int, level_name: str,
                     level_bonus: str, progress, next_bonus: str) -> discord.Embed:
    embed = discord.Embed(title=f"Рефералы — {user.nickname}", colour=discord.Colour.blurple())
    text = level_name if level_name else "—"
    if level_bonus: text += f"\n{level_bonus}"
    embed.add_field(name="Уровень", value=text, inline=False)
    if referred:
        lines = "\n".join(f"• {r['nickname']}" + (f" ({r['date']})" if r.get('date') else "") for r in referred[:25])
        if len(referred) > 25: lines += f"\n… и ещё {_fmt(len(referred) - 25)}"
        embed.add_field(name=f"Приглашено ({_fmt(len(referred))})", value=lines, inline=False)
    else:
        embed.add_field(name="Приглашено", value="Нет приглашённых", inline=False)
    cur, need, nxt = progress
    if nxt:
        bar = "🟦" * min(int(cur / need * 10) if need else 0, 10) + "⬜" * (10 - min(int(cur / need * 10) if need else 0, 10))
        embed.add_field(name=f"До «{nxt}»", value=f"{bar} {_fmt(cur)}/{_fmt(need)}", inline=False)
        if next_bonus: embed.add_field(name="\U0001f381 Награда следующей роли", value=next_bonus, inline=False)
    return embed

# --- Ранги и бонусы (из таблицы) ---
RANK_XP_THRESHOLDS = [50, 250, 1000, 5000, 10000]
RANK_NAMES_GS = ["🔹 Standard", "🔷 Premium", "💠 Prestige", "💎 Elite", "👑 Legend"]
RANK_BONUSES_GS = ["", "└ 🎁 🪙 5 Coin", "└ 🎁 🪙 10 Coins\n└ ⚡ +5% XP\n└ 📊 Скидка 0.5% / Наценка 0.5%",
    "└ 🎁 🪙 40 Coins\n└ 🔥 +2 Coin за сделку >₽50М\n└ 📊 Скидка 1.5% / Наценка 1%\n└ ⏱ Приоритет",
    "└ 🎁 🪙 100 Coins\n└ 🔥 🪙5 за сделку >₽100М\n└ 📊 Скидка 3% / Наценка 1.5%\n└ ⏱ Приоритет + бронь",
    "└ 🎁 🪙 200 Coins\n└ 💸 🪙10/мес\n└ 📈 +1% от счёта/мес (≤🪙15)\n└ 📊 Скидка 5% / Наценка 2%\n└ 🚀 Без очереди, бронь, спец-заказ"]
REF_LEVEL_NAMES = ["", "🧭 Скаут", "📣 Промоутер", "🧲 Вербовщик", "📢 Амбассадор", "🎩 Рекламный Барон"]
REF_LEVEL_BONUSES = ["", "└ 🎁 🪙 1 Coin", "└ 🎁 🪙 5 Coins + ⚡ 10 XP", "└ 🎁 🪙 15 Coins\n└ 🛡 Закрепить 1 раз/нед",
    "└ 🎁 🪙 40 Coins + ⚡ 60 XP\n└ 📉 Скидка 0.5% на бусты",
    "└ 🎁 🪙 150 Coins\n└ 💸 🪙 0.1 с любой сделки\n└ 🎫 Промокод: -1.5% новичку"]
REF_THRESHOLDS = [1, 5, 10, 25, 100]

def _get_rank_index(xp: float) -> int:
    for i, t in enumerate(RANK_XP_THRESHOLDS):
        if xp < t: return i - 1
    return len(RANK_XP_THRESHOLDS) - 1

def _get_rank_progress(xp: float):
    i = _get_rank_index(xp)
    if i >= len(RANK_XP_THRESHOLDS) - 1: return None
    return (int(xp), RANK_XP_THRESHOLDS[i + 1], RANK_NAMES_GS[i + 1])

def _get_rank_bonus(xp: float) -> str:
    i = _get_rank_index(xp) + 1
    return RANK_BONUSES_GS[i] if 0 <= i < len(RANK_BONUSES_GS) else ""

def _get_referral_level(count: int) -> int:
    for i, t in enumerate(REF_THRESHOLDS):
        if count < t: return i
    return len(REF_THRESHOLDS)

def _get_next_level_progress(count: int):
    for i, t in enumerate(REF_THRESHOLDS):
        if count < t:
            prev = REF_THRESHOLDS[i - 1] if i > 0 else 0
            return (count - prev, t - prev, REF_LEVEL_NAMES[i])
    return (count, count, "")

# ------------------------------------------------------------------ #
#  DEAL REPORTS                                                      #
# ------------------------------------------------------------------ #

async def _save_deal_report(channel_id: int, entry: dict) -> None:
    os.makedirs(DEAL_REPORTS_DIR, exist_ok=True)
    path = os.path.join(DEAL_REPORTS_DIR, f"{channel_id}.json")
    def _sync():
        try:
            with open(path, encoding="utf-8") as f: report = json.load(f)
        except: report = []
        report.append(entry)
        with open(path, "w", encoding="utf-8") as f: json.dump(report, f, ensure_ascii=False, indent=2)
    await asyncio.to_thread(_sync)

# ------------------------------------------------------------------ #
#  OCR SERVICE                                                       #
# ------------------------------------------------------------------ #

_ocr_reader = None
def _get_ocr_reader():
    global _ocr_reader
    if _ocr_reader is None:
        import easyocr
        _ocr_reader = easyocr.Reader(["ru", "en"], gpu=False)
    return _ocr_reader

async def _ocr_extract_text(image_bytes: bytes) -> str:
    import numpy as np
    from PIL import Image
    import io as _io
    try:
        img = Image.open(_io.BytesIO(image_bytes)); img_array = np.array(img)
    except Exception as exc:
        raise ValueError(f"Не удалось декодировать изображение: {exc}") from exc
    results = await asyncio.to_thread(_get_ocr_reader().readtext, img_array)
    return "\n".join(t for _, t, c in results if c >= 0.3)

def _ocr_parse_items(text: str) -> list[dict]:
    items = []
    for line in text.split("\n"):
        line = line.strip()
        if not line: continue
        m = re.search(r'(.+?)\s*[xхХ×]\s*(\d[\d\s]*)', line)
        if m:
            name = m.group(1).strip()
            try: qty = int(re.sub(r"\s+", "", m.group(2)))
            except: qty = 1
        else:
            m = re.search(r'(.+?)\s+(\d+)\s*шт', line)
            if m: name, qty = m.group(1).strip(), int(m.group(2))
            else: name, qty = line, 1
        if name: items.append({"name": name, "quantity": qty})
    return items

def _ocr_cross_reference(parsed: list[dict], prices: dict[str, float]):
    readable, total, unknown = [], 0.0, []
    for item in parsed:
        name, qty = item["name"], item["quantity"]
        price = next((v for k, v in prices.items() if k.lower() == name.lower()), None)
        if price is not None:
            lt = price * qty; total += lt
            readable.append(f"{name} x{qty} = {_fmt(lt)} ₽")
        else:
            unknown.append(name)
            readable.append(f"{name} x{qty} = ❓ UNKNOWN")
    return readable, total, unknown

# ------------------------------------------------------------------ #
#  ROLE SERVICE                                                      #
# ------------------------------------------------------------------ #

_role_referrals: dict[str, int] = {}

def _role_load_referrals():
    global _role_referrals
    if os.path.exists(REFERRALS_FILE):
        try:
            with open(REFERRALS_FILE) as f: _role_referrals = json.load(f)
        except: _role_referrals = {}

def _role_save_referrals():
    with open(REFERRALS_FILE, "w", encoding="utf-8") as f:
        json.dump(_role_referrals, f, ensure_ascii=False, indent=2)

def _role_get_referral_count(uid: int) -> int:
    return _role_referrals.get(str(uid), 0)

def _role_increment_referral(uid: int) -> int:
    k = str(uid); c = _role_referrals.get(k, 0) + 1
    _role_referrals[k] = c; _role_save_referrals(); return c

def _get_target_rank_name(volume: int | float) -> Optional[str]:
    t = None
    for n, th in sorted(RANK_THRESHOLDS.items(), key=lambda x: x[1]):
        if volume >= th: t = n
    return t

def _get_target_referral_name(count: int) -> Optional[str]:
    t = None
    for n, th in sorted(REFERRAL_THRESHOLDS.items(), key=lambda x: x[1]):
        if count >= th: t = n
    return t

def _get_current_role_from_map(m: discord.Member, mp: dict[str, int]) -> Optional[discord.Role]:
    mids = {r.id for r in m.roles}
    for rid in mp.values():
        if rid in mids and m.guild: return m.guild.get_role(rid)
    return None

def _get_role_name_by_id(rid: int, mp: dict[str, int]) -> Optional[str]:
    for n, i in mp.items():
        if i == rid: return n
    return None

async def _sync_role(m: discord.Member, target: Optional[str],
                     mp: dict[str, int], label: str) -> Optional[str]:
    tid = mp.get(target) if target else None
    cur = _get_current_role_from_map(m, mp)
    if cur and tid and cur.id == tid: return None
    for rid in mp.values():
        ro = m.guild.get_role(rid) if m.guild else None
        if ro and ro in m.roles:
            try: await m.remove_roles(ro, reason=f"Auto {label} sync")
            except: pass
    if tid and m.guild:
        ro = m.guild.get_role(tid)
        if ro:
            try: await m.add_roles(ro, reason=f"Auto {label}")
            except discord.Forbidden:
                logger.warning("Нет прав на роль %s для %s", ro.name, m)
                return None
            except discord.HTTPException as e:
                logger.error("Ошибка роли %s: %s", ro.name, e); return None
        else:
            logger.warning("Роль %s (group=%s) не найдена", tid, label); return None
    old = _get_role_name_by_id(cur.id, mp) if cur else "(нет)"
    new = target or "(нет)"
    logger.info("[ROLE] %s | %s: %s → %s", m.id, label, old, new)
    return target

async def _assign_rank_role(m: discord.Member, vol: int | float) -> Optional[str]:
    return await _sync_role(m, _get_target_rank_name(vol), RANK_ROLES, "Rank")

async def _assign_referral_role(m: discord.Member, cnt: int) -> Optional[str]:
    return await _sync_role(m, _get_target_referral_name(cnt), REFERRAL_ROLES, "Referral")

# ------------------------------------------------------------------ #
#  АУДИТ                                                             #
# ------------------------------------------------------------------ #

async def _audit_log(bot, user, command: str, details=None, success=True):
    ch = bot.get_channel(AUDIT_CHANNEL_ID)
    if not ch: return
    try:
        labels = {
            "/add": "[ /add ] 📋 Добавление сделки", "/profile": "[ /profile ] 👤 Профиль",
            "/refer": "[ /refer ] 🔗 Назначение реферала", "/referrals": "[ /referrals ] 👥 Рефералы",
            "/tag": "[ /tag ] 💬 Уведомление", "/set_rank": "[ /set_rank ] 🏅 Ранг",
            "/set_referral": "[ /set_referral ] 🔗 Реферальная роль", "/logs": "[ /logs ] 📜 Логи",
            "/setprice": "[ /setprice ] 🏷 Цена ресурса", "/setboost": "[ /setboost ] 🍔 Цена буста",
            "/item_add": "[ /item_add ] ➕ Добавление предмета", "/del_item": "[ /del_item ] 🗑 Удаление предмета",
            "/sync_prices": "[ /sync_prices ] 🔄 Синхронизация цен", "/give_price": "[ /give_price ] 📥 Выгрузка цен",
            "/price_list": "[ /price_list ] 📋 Прайс-лист", "/new_price": "[ /new_price ] 📁 Загрузка цен",
            "/day": "[ /day ] 📊 День", "/week": "[ /week ] 📈 Неделя", "/month": "[ /month ] 📉 Месяц",
        }
        embed = discord.Embed(title=labels.get(command, f"📋 {command}"),
                              colour=discord.Colour.green() if success else discord.Colour.red())
        embed.add_field(name="\U0001f464 Пользователь", value=f"{user.mention} (`{user.id}`)")
        if details:
            if isinstance(details, dict):
                lines = [f"└ {k}: {v}" for k, v in details.items() if v is not None and v != ""]
                embed.add_field(name="\U0001f4dd Детали", value="\n".join(lines), inline=False)
            else:
                embed.add_field(name="\U0001f4dd Детали", value=str(details), inline=False)
        embed.set_footer(text="Успешно" if success else "Ошибка")
        await ch.send(embed=embed)
    except Exception as e:
        logger.warning("audit log failed: %s", e)

# ------------------------------------------------------------------ #
#  АНАЛИТИКА                                                         #
# ------------------------------------------------------------------ #

def _get_transactions_for_period(start: date, end: date) -> list[dict]:
    try: ws = _gs()
    except Exception: return []
    vals = ws.get(f"A{ DATA_START_ROW }:H2000")
    txs, seen_rows = [], set()
    for row in vals:
        if len(row) < 5: continue
        row_key = tuple(row)
        if row_key in seen_rows: continue
        seen_rows.add(row_key)
        try:
            raw_date = str(row[0]).strip() if row[0] else ""
            if not raw_date: continue
            for fmt in ("%d.%m.%y %H:%M", "%d.%m.%Y %H:%M", "%d.%m.%y", "%d.%m.%Y"):
                try: tx_date = datetime.strptime(raw_date, fmt).date(); break
                except: continue
            else: continue
            if not (start <= tx_date <= end): continue
            nickname = str(row[1]).strip() if len(row) > 1 else ""
            is_buy = str(row[2]).strip().upper() == "TRUE" if len(row) > 2 else False
            amount = _gs_parse_float(row[4]) if len(row) > 4 else 0.0
            referrer = str(row[7]).strip() if len(row) > 7 and row[7] else ""
            txs.append({"date": raw_date, "nickname": nickname, "type": "buy" if is_buy else "sell",
                        "amount": amount, "referrer": referrer})
        except: continue
    return txs

def _build_analytics_embed(txs: list[dict], title: str) -> discord.Embed:
    players = {}
    total_buy = total_sell = 0.0
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
        lines.append(f"• {nick}: Покупка {_fmt(data['buy'])}₽ | Продажа {_fmt(data['sell'])}₽ | Оборот {_fmt(total)}₽")
    if lines:
        embed.description = "```" + "\n".join(lines[:30]) + "```"
        if len(lines) > 30: embed.set_footer(text=f"… и ещё {len(lines) - 30} игроков")
    else:
        embed.description = "Нет сделок за выбранный период."
    profit = total_buy - total_sell
    embed.add_field(name="Общий оборот продаж", value=f"{_fmt(total_buy)} ₽")
    embed.add_field(name="Общий оборот покупок", value=f"{_fmt(total_sell)} ₽")
    embed.add_field(name="Чистая прибыль", value=f"{_fmt(profit)} ₽" if profit >= 0 else f"-{_fmt(abs(profit))} ₽")
    return embed

# ------------------------------------------------------------------ #
#  АВТОДОПОЛНЕНИЕ                                                     #
# ------------------------------------------------------------------ #

async def _autocomplete_items(interaction: discord.Interaction, current: str) -> list[app_commands.Choice]:
    items = _db_get_all()
    matches = [it for it in items if current.lower() in it.name.lower()]
    guild = interaction.guild
    return [app_commands.Choice(name=f"{_resolve_emoji(it.emoji, guild)} {it.name}" if it.emoji else it.name, value=it.name)
            for it in matches[:25]]

async def _autocomplete_resources(interaction: discord.Interaction, current: str) -> list[app_commands.Choice]:
    items = [it for it in _db_get_all() if it.category == "resource"]
    matches = [it for it in items if current.lower() in it.name.lower()]
    guild = interaction.guild
    return [app_commands.Choice(name=f"{_resolve_emoji(it.emoji, guild)} {it.name}" if it.emoji else it.name, value=it.name)
            for it in matches[:25]]

async def _autocomplete_boosts(interaction: discord.Interaction, current: str) -> list[app_commands.Choice]:
    items = [it for it in _db_get_all() if it.category == "boost"]
    matches = [it for it in items if current.lower() in it.name.lower()]
    guild = interaction.guild
    return [app_commands.Choice(name=f"{_resolve_emoji(it.emoji, guild)} {it.name}" if it.emoji else it.name, value=it.name)
            for it in matches[:25]]

# ------------------------------------------------------------------ #
#  VIEW & MODAL для формы тикета                                     #
# ------------------------------------------------------------------ #

class TicketFormView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    @discord.ui.button(label="📝 Заполнить форму", style=discord.ButtonStyle.primary, custom_id="ticket_form:open")
    async def open_form(self, interaction: discord.Interaction, _b: discord.ui.Button):
        ch_id = interaction.channel.category_id if hasattr(interaction.channel, "category_id") else interaction.channel_id
        cat = next((n for n, c in CATEGORY_CHANNELS.items() if c == ch_id), None)
        if cat is None:
            await interaction.response.send_message("Это не канал тикета.", ephemeral=True); return
        await interaction.response.send_modal(TicketFormModal(cat))

class TicketFormModal(discord.ui.Modal):
    def __init__(self, category: str):
        super().__init__(title=f"Форма — {category}"); self.category = category
        for f in CATEGORY_FIELDS.get(category, []):
            self.add_item(discord.ui.TextInput(label=f["label"], custom_id=f["custom_id"],
                required=f["required"], style=f.get("style", discord.TextStyle.short),
                placeholder=f.get("placeholder", ""), max_length=f.get("max_length", 4000)))
    async def on_submit(self, interaction: discord.Interaction):
        data = {c.custom_id: c.value for c in self.children if isinstance(c, discord.ui.TextInput)}
        entry = {"type": "form", "timestamp": discord.utils.utcnow().isoformat(),
                 "user_id": interaction.user.id, "user_name": str(interaction.user),
                 "category": self.category, "data": data}
        await _save_deal_report(interaction.channel_id, entry)
        await interaction.response.send_message("✅ Форма отправлена!", ephemeral=True)

# ------------------------------------------------------------------ #
#  PRICE LIST VIEW (с переключением ресурсы/бусты)                    #
# ------------------------------------------------------------------ #

class PriceListView(discord.ui.View):
    def __init__(self, resources: list[Item], boosts: list[Item],
                 page: int = 0, show_resources: bool = True, guild=None):
        super().__init__(timeout=120)
        self.resources = resources; self.boosts = boosts
        self.page = page; self.show_resources = show_resources
        self.per_page = 15; self._guild = guild

    def _build_embed(self) -> discord.Embed:
        items = self.resources if self.show_resources else self.boosts
        label = "Ресурсы (Скупка)" if self.show_resources else "Бусты (Продажа)"
        start = self.page * self.per_page
        chunk = items[start:start + self.per_page]
        embed = discord.Embed(title=f"📋 Прайс-лист — {label}", colour=discord.Colour.blue())
        total_pages = max(1, math.ceil(len(items) / self.per_page))
        for it in chunk:
            price = _fmt(it.price_buy) if self.show_resources else _fmt(it.price_sell)
            e = _resolve_emoji(it.emoji, self._guild)
            emoji = e + " " if e else ""
            embed.add_field(name=f"{emoji}{it.name}", value=f"{price} ₽", inline=True)
        embed.set_footer(text=f"Страница {self.page + 1}/{total_pages} • Всего: {len(items)}")
        return embed

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary, custom_id="price_prev")
    async def prev(self, i: discord.Interaction, _b: discord.ui.Button):
        items = self.resources if self.show_resources else self.boosts
        total = max(1, math.ceil(len(items) / self.per_page))
        self.page = (self.page - 1) % total
        await i.response.edit_message(embed=self._build_embed(), view=self)

    @discord.ui.button(label="🔄 Сменить категорию", style=discord.ButtonStyle.primary, custom_id="price_toggle")
    async def toggle(self, i: discord.Interaction, _b: discord.ui.Button):
        self.show_resources = not self.show_resources; self.page = 0
        await i.response.edit_message(embed=self._build_embed(), view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary, custom_id="price_next")
    async def nxt(self, i: discord.Interaction, _b: discord.ui.Button):
        items = self.resources if self.show_resources else self.boosts
        total = max(1, math.ceil(len(items) / self.per_page))
        self.page = (self.page + 1) % total
        await i.response.edit_message(embed=self._build_embed(), view=self)

# =================================================================== #
#  (d)  СОБЫТИЯ                                                       #
# =================================================================== #

async def _ensure_form_messages(bot: commands.Bot) -> None:
    view = TicketFormView(); bot.add_view(view)
    for category, cid in CATEGORY_CHANNELS.items():
        ch = bot.get_channel(cid)
        if ch is None:
            logger.warning("Канал %s (ID %s) не найден", category, cid); continue
        # Если это категория — берём первый текстовый канал внутри
        if isinstance(ch, discord.CategoryChannel):
            text_ch = next((c for c in ch.text_channels), None)
            if text_ch is None:
                logger.warning("В категории %s нет текстовых каналов", category); continue
            ch = text_ch
        try:
            exists = False
            async for msg in ch.history(limit=30):
                if msg.author == bot.user and msg.components: exists = True; break
            if not exists:
                embed = discord.Embed(title=f"📋 {category}",
                    description="Для оформления сделки нажмите кнопку ниже и заполните форму.\n"
                                "Вы также можете прикрепить скриншот — бот автоматически распознает предметы и рассчитает сумму.",
                    colour=discord.Colour.blurple())
                await ch.send(embed=embed, view=view)
        except Exception:
            logger.exception("Ошибка при создании формы в %s", category)

# =================================================================== #
#  (e)  SLASH-КОМАНДЫ (в алфавитном порядке)                          #
# =================================================================== #

async def setup_commands(bot: commands.Bot) -> None:

    # ---------------------------------------------------------------- #
    #  /add
    # ---------------------------------------------------------------- #
    @bot.tree.command(name="add", description="📝 (Админ) Записать новую сделку в Google Таблицу (сумма, тип, ник) с авто-калькулятором")
    @app_commands.describe(тип="Тип сделки", ник="Ник игрока", сумма="Сумма сделки (поддерживает выражения: 1500*3+200)", ник_пригласившего="Ник того, кто пригласил (необязательно)")
    @app_commands.choices(тип=[app_commands.Choice(name="Покупка", value="buy"), app_commands.Choice(name="Продажа", value="sell")])
    @app_commands.checks.has_permissions(administrator=True)
    async def add(interaction: discord.Interaction, тип: str, ник: str, сумма: str, ник_пригласившего: Optional[str] = None):
        await _safe_defer(interaction)

        nickname = ник.strip()
        if not nickname:
            await interaction.followup.send(embed=_error_embed("Ник не может быть пустым."), ephemeral=True)
            return

        referrer = ник_пригласившего.strip() if ник_пригласившего else None
        if referrer:
            if referrer.lower() == nickname.lower():
                await interaction.followup.send(embed=_error_embed("Нельзя указывать самого себя."), ephemeral=True)
                return

        try:
            amount = safe_calc(сумма)
        except Exception:
            await interaction.followup.send(embed=_error_embed("Некорректное выражение в сумме."), ephemeral=True)
            return
        if amount <= 0:
            await interaction.followup.send(embed=_error_embed("Сумма должна быть больше 0"), ephemeral=True)
            return
        try:
            await asyncio.to_thread(_gs_add_transaction, nickname, тип, amount, referrer)
        except Exception as e:
            logger.error("add error: %s", e)
            await interaction.followup.send(embed=_error_embed(f"Ошибка при сохранении: {e}"), ephemeral=True)
            return
        embed = discord.Embed(title="Сделка зафиксирована", colour=discord.Colour.green())
        embed.add_field(name="Ник", value=nickname)
        embed.add_field(name="Тип", value="Покупка" if тип == "buy" else "Продажа")
        embed.add_field(name="Сумма", value=_fmt(amount))
        if referrer: embed.add_field(name="Ник пригласившего", value=referrer)
        await interaction.followup.send(embed=embed, ephemeral=True)
        try:
            details = {"Никнейм": nickname, "Тип": тип, "Сумма": _fmt(amount)}
            if referrer:
                details["Реферер"] = referrer
            await _audit_log(interaction.client, interaction.user, "/add", details)
        except: pass

    @add.error
    async def add_error(i: discord.Interaction, e: app_commands.AppCommandError):
        if isinstance(e, app_commands.MissingPermissions):
            try: await i.response.send_message("Недостаточно прав. Требуются права администратора.", ephemeral=True)
            except: await i.followup.send("Недостаточно прав. Требуются права администратора.", ephemeral=True)
        else:
            try: await i.response.send_message(f"Ошибка: {e}", ephemeral=True)
            except: await i.followup.send(f"Ошибка: {e}", ephemeral=True)

    # ---------------------------------------------------------------- #
    #  /profile
    # ---------------------------------------------------------------- #
    @bot.tree.command(name="profile", description="👤 Показать профиль пользователя")
    @app_commands.describe(nickname="Ваш ник в таблице")
    async def profile(interaction: discord.Interaction, nickname: str):
        await _safe_defer(interaction)
        try:
            u = await asyncio.to_thread(_gs_get_user_profile, nickname)
        except Exception as e:
            logger.error("profile error: %s", e)
            await interaction.followup.send(embed=_error_embed("Ошибка загрузки профиля"), ephemeral=True); return
        if u is None:
            await interaction.followup.send(embed=_error_embed("Пользователь не найден"), ephemeral=True); return
        await interaction.followup.send(embed=_profile_embed(u, _get_rank_progress(u.xp), _get_rank_bonus(u.xp)), ephemeral=True)
        try: await _audit_log(interaction.client, interaction.user, "/profile", {"Никнейм": nickname})
        except: pass

    # ---------------------------------------------------------------- #
    #  /refer
    # ---------------------------------------------------------------- #
    @bot.tree.command(name="refer", description="🔗 (Админ) Привязать реферала вручную")
    @app_commands.describe(ник_игрока="Ник в таблице", ник_пригласившего="Кто пригласил")
    @app_commands.checks.has_permissions(administrator=True)
    async def refer(interaction: discord.Interaction, ник_игрока: str, ник_пригласившего: str):
        await _safe_defer(interaction)
        if ник_игрока.lower() == ник_пригласившего.lower():
            await interaction.followup.send(embed=_error_embed("Нельзя указать самого себя"), ephemeral=True); return
        try:
            ex = await asyncio.to_thread(_gs_find_user, ник_игрока)
            if ex and ex.get("referred_by"):
                await interaction.followup.send(embed=_error_embed("Реферал уже указан, изменить нельзя"), ephemeral=True); return
            await asyncio.to_thread(_gs_set_referred_by, ник_игрока, ник_пригласившего)
        except Exception as e:
            logger.error("refer error: %s", e)
            await interaction.followup.send(embed=_error_embed("Ошибка при установке реферала"), ephemeral=True); return
        await interaction.followup.send(embed=_referral_embed(ник_пригласившего), ephemeral=True)
        try: await _audit_log(interaction.client, interaction.user, "/refer", {"Ник игрока": ник_игрока, "Ник пригласившего": ник_пригласившего})
        except: pass

    @refer.error
    async def refer_error(i: discord.Interaction, e: app_commands.AppCommandError):
        if isinstance(e, app_commands.MissingPermissions):
            try: await i.response.send_message("Недостаточно прав. Требуются права администратора.", ephemeral=True)
            except: await i.followup.send("Недостаточно прав. Требуются права администратора.", ephemeral=True)

    # ---------------------------------------------------------------- #
    #  /referrals
    # ---------------------------------------------------------------- #
    @bot.tree.command(name="referrals", description="👥 Показать список рефералов пользователя и дату прикрепления")
    @app_commands.describe(nickname="Ваш ник в таблице")
    async def referrals(interaction: discord.Interaction, nickname: str):
        await _safe_defer(interaction)
        try:
            u = await asyncio.to_thread(_gs_get_user_profile, nickname)
            refs = await asyncio.to_thread(_gs_get_referred_users, nickname)
        except Exception as e:
            logger.error("referrals error: %s", e)
            await interaction.followup.send(embed=_error_embed("Ошибка загрузки"), ephemeral=True); return
        if u is None:
            await interaction.followup.send(embed=_error_embed("Пользователь не найден"), ephemeral=True); return
        count = len(refs); lvl = _get_referral_level(count)
        ln = REF_LEVEL_NAMES[lvl] if lvl < len(REF_LEVEL_NAMES) else ""
        lb = REF_LEVEL_BONUSES[lvl] if lvl < len(REF_LEVEL_BONUSES) else ""
        prog = _get_next_level_progress(count)
        nb = REF_LEVEL_BONUSES[lvl + 1] if lvl + 1 < len(REF_LEVEL_BONUSES) else ""
        await interaction.followup.send(embed=_referrals_embed(u, refs, lvl, ln, lb, prog, nb), ephemeral=True)
        try: await _audit_log(interaction.client, interaction.user, "/referrals", {"Никнейм": nickname, "Рефералов": count})
        except: pass

    # ---------------------------------------------------------------- #
    #  /logs
    # ---------------------------------------------------------------- #
    @bot.tree.command(name="logs", description="📜 (Админ) Вывести полные логи всех совершенных сделок от новых к старым")
    @app_commands.checks.has_permissions(administrator=True)
    async def logs(interaction: discord.Interaction):
        await _safe_defer(interaction)
        try:
            ws = _gs(); vals = ws.get(f"A{ DATA_START_ROW }:H2000")
        except Exception as e:
            await interaction.followup.send(embed=_error_embed(f"Ошибка: {e}"), ephemeral=True); return
        lines, seen = [], set()
        for row in reversed(vals):
            if len(row) < 5: continue
            key = tuple(row)
            if key in seen: continue; seen.add(key)
            raw_date = str(row[0]).strip() if row[0] else ""; nick = str(row[1]).strip() if len(row) > 1 else ""
            t = "Покупка" if len(row) > 2 and str(row[2]).strip().upper() == "TRUE" else "Продажа"
            amt = _gs_parse_float(row[4]) if len(row) > 4 else 0.0
            ref = str(row[7]).strip() if len(row) > 7 and row[7] else ""
            line = f"[{raw_date}] {nick} | {t} | {_fmt(amt)}₽"
            if ref: line += f" | Реферер: {ref}"
            lines.append(line)
            if len(lines) >= 50: break
        if not lines:
            await interaction.followup.send("Нет записей.", ephemeral=True); return
        text = "```" + "\n".join(lines) + "```"
        if len(text) > 1900:
            text = "```" + "\n".join(lines[:30]) + f"\n… и ещё {len(lines) - 30} записей" + "```"
        await interaction.followup.send(text, ephemeral=True)
        try: await _audit_log(interaction.client, interaction.user, "/logs")
        except: pass

    @logs.error
    async def logs_error(i: discord.Interaction, e: app_commands.AppCommandError):
        if isinstance(e, app_commands.MissingPermissions):
            try: await i.response.send_message("Недостаточно прав. Требуются права администратора.", ephemeral=True)
            except: await i.followup.send("Недостаточно прав. Требуются права администратора.", ephemeral=True)

    # ---------------------------------------------------------------- #
    #  /setprice
    # ---------------------------------------------------------------- #
    @bot.tree.command(name="setprice", description="🏷 (Админ) Изменить цену ресурса или скупки буста в базе данных и таблице")
    @app_commands.describe(item="Название предмета", price="Новая цена скупки")
    @app_commands.autocomplete(item=_autocomplete_resources)
    @app_commands.checks.has_permissions(administrator=True)
    async def setprice(interaction: discord.Interaction, item: str, price: str):
        await _safe_defer(interaction)
        try: amount = safe_calc(price)
        except Exception:
            await interaction.followup.send(embed=_error_embed("Некорректная цена."), ephemeral=True); return
        try:
            it = _db_get_items().get(item.lower())
            if it is None:
                await interaction.followup.send(embed=_error_embed("Предмет не найден в базе."), ephemeral=True); return
            _db_upsert(it.name, it.category, price_buy=amount)
            _sync_prices_from_db()
            e = _resolve_emoji(it.emoji, interaction.guild)
            emoji = e + " " if e else ""
            await interaction.followup.send(
                f"✅ {emoji}{it.name}: цена скупки изменена на {_fmt(amount)} ₽", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(embed=_error_embed(f"Ошибка: {e}"), ephemeral=True)
        try: await _audit_log(interaction.client, interaction.user, "/setprice", {"Предмет": item, "Цена": _fmt(amount)})
        except: pass

    @setprice.error
    async def setprice_error(i: discord.Interaction, e: app_commands.AppCommandError):
        if isinstance(e, app_commands.MissingPermissions):
            try: await i.response.send_message("Недостаточно прав. Требуются права администратора.", ephemeral=True)
            except: await i.followup.send("Недостаточно прав. Требуются права администратора.", ephemeral=True)

    # ---------------------------------------------------------------- #
    #  /setboost
    # ---------------------------------------------------------------- #
    @bot.tree.command(name="setboost", description="🍔 (Админ) Изменить цену продажи буста игрокам")
    @app_commands.describe(item="Название буста", price="Новая цена продажи")
    @app_commands.autocomplete(item=_autocomplete_boosts)
    @app_commands.checks.has_permissions(administrator=True)
    async def setboost(interaction: discord.Interaction, item: str, price: str):
        await _safe_defer(interaction)
        try: amount = safe_calc(price)
        except Exception:
            await interaction.followup.send(embed=_error_embed("Некорректная цена."), ephemeral=True); return
        try:
            it = _db_get_items().get(item.lower())
            if it is None:
                await interaction.followup.send(embed=_error_embed("Буст не найден в базе."), ephemeral=True); return
            _db_upsert(it.name, it.category, price_sell=amount)
            _sync_prices_from_db()
            e = _resolve_emoji(it.emoji, interaction.guild)
            emoji = e + " " if e else ""
            await interaction.followup.send(
                f"✅ {emoji}{it.name}: цена продажи изменена на {_fmt(amount)} ₽", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(embed=_error_embed(f"Ошибка: {e}"), ephemeral=True)
        try: await _audit_log(interaction.client, interaction.user, "/setboost", {"Предмет": item, "Цена": _fmt(amount)})
        except: pass

    @setboost.error
    async def setboost_error(i: discord.Interaction, e: app_commands.AppCommandError):
        if isinstance(e, app_commands.MissingPermissions):
            try: await i.response.send_message("Недостаточно прав. Требуются права администратора.", ephemeral=True)
            except: await i.followup.send("Недостаточно прав. Требуются права администратора.", ephemeral=True)

    # ---------------------------------------------------------------- #
    #  /item_add
    # ---------------------------------------------------------------- #
    @bot.tree.command(name="item_add", description="➕ (Админ) Добавить новый предмет или буст в общую базу данных")
    @app_commands.describe(name="Название предмета/буста", category="Тип: resource — скупка, boost — продажа",
                           price_buy="Цена скупки (для resource)", price_sell="Цена продажи (для boost)", emoji="Эмодзи (необязательно)")
    @app_commands.choices(category=[
        app_commands.Choice(name="Ресурс (скупка)", value="resource"),
        app_commands.Choice(name="Буст (продажа)", value="boost"),
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def item_add(interaction: discord.Interaction, name: str, category: str,
                       price_buy: Optional[str] = None, price_sell: Optional[str] = None, emoji: str = ""):
        await _safe_defer(interaction)
        try:
            pb = safe_calc(price_buy) if price_buy else None
        except: pb = None
        try:
            ps = safe_calc(price_sell) if price_sell else None
        except: ps = None
        try:
            it = _db_upsert(name.strip(), category, price_buy=pb, price_sell=ps, emoji=emoji.strip())
            _sync_prices_from_db()
            e = _resolve_emoji(it.emoji, interaction.guild)
            emoji_str = e + " " if e else ""
            await interaction.followup.send(f"✅ {emoji_str}{it.name} добавлен (ID: {it.id})", ephemeral=True)
        except Exception as ex:
            await interaction.followup.send(embed=_error_embed(f"Ошибка: {ex}"), ephemeral=True)
        try: await _audit_log(interaction.client, interaction.user, "/item_add",
                              {"Название": name, "Категория": category})
        except: pass

    @item_add.error
    async def item_add_error(i: discord.Interaction, e: app_commands.AppCommandError):
        if isinstance(e, app_commands.MissingPermissions):
            try: await i.response.send_message("Недостаточно прав. Требуются права администратора.", ephemeral=True)
            except: await i.followup.send("Недостаточно прав. Требуются права администратора.", ephemeral=True)

    # ---------------------------------------------------------------- #
    #  /del_item
    # ---------------------------------------------------------------- #
    @bot.tree.command(name="del_item", description="🗑 (Админ) Удалить выбранный предмет из базы данных")
    @app_commands.describe(item="Название предмета для удаления")
    @app_commands.autocomplete(item=_autocomplete_items)
    @app_commands.checks.has_permissions(administrator=True)
    async def del_item(interaction: discord.Interaction, item: str):
        await _safe_defer(interaction)
        try:
            if _db_delete(item):
                _sync_prices_from_db()
                await interaction.followup.send(f"✅ {item} удалён из базы.", ephemeral=True)
            else:
                await interaction.followup.send(embed=_error_embed("Предмет не найден."), ephemeral=True)
        except Exception as e:
            await interaction.followup.send(embed=_error_embed(f"Ошибка: {e}"), ephemeral=True)
        try: await _audit_log(interaction.client, interaction.user, "/del_item", {"Предмет": item})
        except: pass

    @del_item.error
    async def del_item_error(i: discord.Interaction, e: app_commands.AppCommandError):
        if isinstance(e, app_commands.MissingPermissions):
            try: await i.response.send_message("Недостаточно прав. Требуются права администратора.", ephemeral=True)
            except: await i.followup.send("Недостаточно прав. Требуются права администратора.", ephemeral=True)

    # ---------------------------------------------------------------- #
    #  /sync_prices
    # ---------------------------------------------------------------- #
    @bot.tree.command(name="sync_prices", description="🔄 (Админ) Принудительно выгрузить и обновить актуальные цены в основную таблицу")
    @app_commands.checks.has_permissions(administrator=True)
    async def sync_prices(interaction: discord.Interaction):
        await _safe_defer(interaction)
        try:
            cnt = _sync_prices_from_db()
            await interaction.followup.send(f"✅ Синхронизация завершена. Выгружено {len(cnt)} цен.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(embed=_error_embed(f"Ошибка: {e}"), ephemeral=True)
        try: await _audit_log(interaction.client, interaction.user, "/sync_prices")
        except: pass

    @sync_prices.error
    async def sync_prices_error(i: discord.Interaction, e: app_commands.AppCommandError):
        if isinstance(e, app_commands.MissingPermissions):
            try: await i.response.send_message("Недостаточно прав. Требуются права администратора.", ephemeral=True)
            except: await i.followup.send("Недостаточно прав. Требуются права администратора.", ephemeral=True)

    # ---------------------------------------------------------------- #
    #  /tag
    # ---------------------------------------------------------------- #
    @bot.tree.command(name="tag", description="💬 (Админ) Отправить уведомление пользователю в личные сообщения о тикете")
    @app_commands.describe(user="Уведомить участника о тикете в личных сообщениях")
    @app_commands.checks.has_permissions(administrator=True)
    async def tag(interaction: discord.Interaction, user: discord.User):
        await _safe_defer(interaction)
        embed = discord.Embed(title="📢 Уведомление по тикету",
            description=f"Здравствуйте, {user.mention}!\nВ вашем активном тикете {interaction.channel.mention} поступило новое сообщение. Команда ожидает вашего ответа, чтобы продолжить сделку или решить вопрос.\nПожалуйста, вернитесь в чат, когда будете готовы!",
            colour=discord.Color.brand_green())
        embed.add_field(name="🔮 От Главы Шёпота", value="Команда «Клондайк Шёпота»", inline=False)
        icon = interaction.guild.icon.url if interaction.guild and interaction.guild.icon else None
        embed.set_footer(text="Маркетплейс «Клондайк Шёпота»", icon_url=icon)
        embed.timestamp = discord.utils.utcnow()
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="🔗 Перейти к тикету", url=interaction.channel.jump_url, style=discord.ButtonStyle.link))
        try:
            await user.send(embed=embed, view=view)
            await interaction.followup.send(f"✅ Уведомление отправлено {user.mention}.", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("Не удалось отправить сообщение пользователю.", ephemeral=True)
        try: await _audit_log(interaction.client, interaction.user, "/tag", {"Пользователь": str(user)})
        except: pass

    @tag.error
    async def tag_error(i: discord.Interaction, e: app_commands.AppCommandError):
        if isinstance(e, app_commands.MissingPermissions):
            try: await i.response.send_message("Недостаточно прав. Требуются права администратора.", ephemeral=True)
            except: await i.followup.send("Недостаточно прав. Требуются права администратора.", ephemeral=True)

    # ---------------------------------------------------------------- #
    #  /give_price
    # ---------------------------------------------------------------- #
    @bot.tree.command(name="give_price", description="📥 (Админ) Скачать файл с текущими ценами")
    @app_commands.checks.has_permissions(administrator=True)
    async def give_price(interaction: discord.Interaction):
        await _safe_defer(interaction)
        try:
            items = _db_get_all()
            buf = io.StringIO(); w = csv.writer(buf)
            w.writerow(["ID", "Название", "Категория", "Цена скупки", "Цена продажи", "Эмодзи", "Обновлено"])
            for it in items:
                w.writerow([it.id, it.name, it.category,
                           _fmt(it.price_buy) if it.price_buy else "",
                           _fmt(it.price_sell) if it.price_sell else "",
                           it.emoji, it.updated_at])
            buf.seek(0)
            file = discord.File(io.BytesIO(buf.getvalue().encode("utf-8-sig")), filename="prices.csv")
            await interaction.followup.send(file=file, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(embed=_error_embed(f"Ошибка: {e}"), ephemeral=True)
        try: await _audit_log(interaction.client, interaction.user, "/give_price")
        except: pass

    @give_price.error
    async def give_price_error(i: discord.Interaction, e: app_commands.AppCommandError):
        if isinstance(e, app_commands.MissingPermissions):
            try: await i.response.send_message("Недостаточно прав.", ephemeral=True)
            except: await i.followup.send("Недостаточно прав.", ephemeral=True)

    # ---------------------------------------------------------------- #
    #  /price_list
    # ---------------------------------------------------------------- #
    @bot.tree.command(name="price_list", description="📋 (Админ) Показать прайс-лист ресурсов с кнопкой переключения на бусты")
    @app_commands.checks.has_permissions(administrator=True)
    async def price_list(interaction: discord.Interaction):
        await _safe_defer(interaction)
        try:
            items = _db_get_all()
            resources = [it for it in items if it.category == "resource"]
            boosts = [it for it in items if it.category == "boost"]
            view = PriceListView(resources, boosts, guild=interaction.guild)
            await interaction.followup.send(embed=view._build_embed(), view=view, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(embed=_error_embed(f"Ошибка: {e}"), ephemeral=True)
        try: await _audit_log(interaction.client, interaction.user, "/price_list")
        except: pass

    @price_list.error
    async def price_list_error(i: discord.Interaction, e: app_commands.AppCommandError):
        if isinstance(e, app_commands.MissingPermissions):
            try: await i.response.send_message("Недостаточно прав.", ephemeral=True)
            except: await i.followup.send("Недостаточно прав.", ephemeral=True)

    # ---------------------------------------------------------------- #
    #  /new_price
    # ---------------------------------------------------------------- #
    @bot.tree.command(name="new_price", description="📁 (Админ) Загрузить новый прайс-лист для массового обновления")
    @app_commands.describe(file="CSV-файл с колонками: Название, Категория, Цена скупки, Цена продажи, Эмодзи")
    @app_commands.checks.has_permissions(administrator=True)
    async def new_price(interaction: discord.Interaction, file: discord.Attachment):
        await _safe_defer(interaction)
        if not file.filename.endswith(".csv"):
            await interaction.followup.send(embed=_error_embed("Файл должен быть в формате CSV."), ephemeral=True); return
        try:
            content = (await file.read()).decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(content))
            old_items = {it.name.lower(): it for it in _db_get_all()}
            count = 0
            changes = []
            for row in reader:
                name = row.get("Название", "").strip()
                cat = row.get("Категория", "").strip()
                pb = _gs_parse_float(row.get("Цена скупки", "")) if row.get("Цена скупки", "").strip() else None
                ps = _gs_parse_float(row.get("Цена продажи", "")) if row.get("Цена продажи", "").strip() else None
                emoji = row.get("Эмодзи", "").strip()
                if name and cat:
                    old = old_items.get(name.lower())
                    old_pb = old.price_buy if old else None
                    old_ps = old.price_sell if old else None
                    _db_upsert(name, cat, price_buy=pb, price_sell=ps, emoji=emoji)
                    count += 1
                    if old and (old_pb != pb or old_ps != ps):
                        parts = []
                        if old_pb != pb:
                            parts.append(f"Скупка: {_fmt(old_pb) if old_pb is not None else '—'} → {_fmt(pb) if pb is not None else '—'}")
                        if old_ps != ps:
                            parts.append(f"Продажа: {_fmt(old_ps) if old_ps is not None else '—'} → {_fmt(ps) if ps is not None else '—'}")
                        e = _resolve_emoji(old.emoji if old else "", interaction.guild)
                        e_str = e + " " if e else ""
                        changes.append(f"• {e_str}{name} | {'; '.join(parts)}")
            _sync_prices_from_db()
            msg = f"✅ Импортировано {count} позиций."
            if changes:
                chunks = []
                chunk = "**Изменения цен:**\n"
                for c in changes:
                    if len(chunk) + len(c) + 1 > 1900:
                        chunks.append(chunk)
                        chunk = ""
                    chunk += c + "\n"
                if chunk:
                    chunks.append(chunk)
                for c in chunks:
                    await interaction.followup.send(c, ephemeral=True)
            await interaction.followup.send(msg, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(embed=_error_embed(f"Ошибка: {e}"), ephemeral=True)
        try: await _audit_log(interaction.client, interaction.user, "/new_price", {"Файл": file.filename})
        except: pass

    @new_price.error
    async def new_price_error(i: discord.Interaction, e: app_commands.AppCommandError):
        if isinstance(e, app_commands.MissingPermissions):
            try: await i.response.send_message("Недостаточно прав.", ephemeral=True)
            except: await i.followup.send("Недостаточно прав.", ephemeral=True)

    # ---------------------------------------------------------------- #
    #  /day
    # ---------------------------------------------------------------- #
    @bot.tree.command(name="day", description="📊 (Админ) Показать аналитику и статистику продаж за конкретный день")
    @app_commands.describe(дата="Дата в формате ДД.ММ.ГГГГ (например 26.07.2026)")
    @app_commands.checks.has_permissions(administrator=True)
    async def day(interaction: discord.Interaction, дата: str):
        await _safe_defer(interaction)
        try:
            d = datetime.strptime(дата.strip(), "%d.%m.%Y").date()
        except:
            await interaction.followup.send(embed=_error_embed("Неверный формат даты. Используйте ДД.ММ.ГГГГ"), ephemeral=True); return
        try:
            txs = await asyncio.to_thread(_get_transactions_for_period, d, d)
            embed = _build_analytics_embed(txs, f"📊 Статистика за {d.strftime('%d.%m.%Y')}")
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(embed=_error_embed(f"Ошибка: {e}"), ephemeral=True)
        try: await _audit_log(interaction.client, interaction.user, "/day", {"Дата": дата})
        except: pass

    @day.error
    async def day_error(i: discord.Interaction, e: app_commands.AppCommandError):
        if isinstance(e, app_commands.MissingPermissions):
            try: await i.response.send_message("Недостаточно прав.", ephemeral=True)
            except: await i.followup.send("Недостаточно прав.", ephemeral=True)

    # ---------------------------------------------------------------- #
    #  /week
    # ---------------------------------------------------------------- #
    @bot.tree.command(name="week", description="📈 (Админ) Показать статистику продаж за выбранную неделю")
    @app_commands.describe(год="Год (например 2026)", неделя="Номер недели (1-53)")
    @app_commands.checks.has_permissions(administrator=True)
    async def week(interaction: discord.Interaction, год: int, неделя: int):
        await _safe_defer(interaction)
        try:
            start = datetime.strptime(f"{год}-W{неделя:02d}-1", "%G-W%V-%u").date()
            end = start + timedelta(days=6)
        except:
            await interaction.followup.send(embed=_error_embed("Неверный номер недели."), ephemeral=True); return
        try:
            txs = await asyncio.to_thread(_get_transactions_for_period, start, end)
            embed = _build_analytics_embed(txs, f"📈 Статистика за {неделя}-ю неделю {год} ({start.strftime('%d.%m')}-{end.strftime('%d.%m')})")
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(embed=_error_embed(f"Ошибка: {e}"), ephemeral=True)
        try: await _audit_log(interaction.client, interaction.user, "/week", {"Год": год, "Неделя": неделя})
        except: pass

    @week.error
    async def week_error(i: discord.Interaction, e: app_commands.AppCommandError):
        if isinstance(e, app_commands.MissingPermissions):
            try: await i.response.send_message("Недостаточно прав.", ephemeral=True)
            except: await i.followup.send("Недостаточно прав.", ephemeral=True)

    # ---------------------------------------------------------------- #
    #  /month
    # ---------------------------------------------------------------- #
    @bot.tree.command(name="month", description="📉 (Админ) Показать аналитику и статистику продаж за полный месяц")
    @app_commands.describe(месяц="Месяц (1-12)", год="Год (например 2026)")
    @app_commands.checks.has_permissions(administrator=True)
    async def month(interaction: discord.Interaction, месяц: int, год: int):
        await _safe_defer(interaction)
        if not 1 <= месяц <= 12:
            await interaction.followup.send(embed=_error_embed("Месяц должен быть от 1 до 12."), ephemeral=True); return
        try:
            import calendar
            start = date(год, месяц, 1)
            end = date(год, месяц, calendar.monthrange(год, месяц)[1])
            txs = await asyncio.to_thread(_get_transactions_for_period, start, end)
            embed = _build_analytics_embed(txs, f"📉 Статистика за {start.strftime('%B %Y')} ({start.strftime('%d.%m')}-{end.strftime('%d.%m')})")
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(embed=_error_embed(f"Ошибка: {e}"), ephemeral=True)
        try: await _audit_log(interaction.client, interaction.user, "/month", {"Месяц": месяц, "Год": год})
        except: pass

    @month.error
    async def month_error(i: discord.Interaction, e: app_commands.AppCommandError):
        if isinstance(e, app_commands.MissingPermissions):
            try: await i.response.send_message("Недостаточно прав.", ephemeral=True)
            except: await i.followup.send("Недостаточно прав.", ephemeral=True)

    # ---------------------------------------------------------------- #
    #  /set_rank
    # ---------------------------------------------------------------- #
    @bot.tree.command(name="set_rank", description="🏅 (Админ) Вручную установить ранговую роль участнику")
    @app_commands.describe(user="Участник", rank="Название ранга")
    @app_commands.choices(rank=[
        app_commands.Choice(name="Standard", value="Standard"),
        app_commands.Choice(name="Premium", value="Premium"),
        app_commands.Choice(name="Prestige", value="Prestige"),
        app_commands.Choice(name="Elite", value="Elite"),
        app_commands.Choice(name="Legend", value="Legend"),
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def set_rank(interaction: discord.Interaction, user: discord.Member, rank: str):
        await _safe_defer(interaction)
        rid = RANK_ROLES.get(rank)
        if rid is None:
            await interaction.followup.send(embed=_error_embed("Ранг не найден"), ephemeral=True); return
        ro = interaction.guild.get_role(rid)
        if ro is None:
            await interaction.followup.send(embed=_error_embed("Роль не найдена на сервере"), ephemeral=True); return
        try:
            # снимаем старые ранговые роли
            for rid_old in RANK_ROLES.values():
                r_old = interaction.guild.get_role(rid_old)
                if r_old and r_old in user.roles:
                    await user.remove_roles(r_old, reason="set_rank")
            await user.add_roles(ro, reason="set_rank")
            await interaction.followup.send(f"✅ Роль `{rank}` назначена {user.mention}.", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send(embed=_error_embed("Нет прав на назначение роли"), ephemeral=True)

    @set_rank.error
    async def set_rank_error(i: discord.Interaction, e: app_commands.AppCommandError):
        if isinstance(e, app_commands.MissingPermissions):
            try: await i.response.send_message("Недостаточно прав. Требуются права администратора.", ephemeral=True)
            except: await i.followup.send("Недостаточно прав. Требуются права администратора.", ephemeral=True)

    # ---------------------------------------------------------------- #
    #  /set_referral
    # ---------------------------------------------------------------- #
    @bot.tree.command(name="set_referral", description="🔗 (Админ) Вручную установить реферальную роль участнику")
    @app_commands.describe(user="Участник", role="Название реферальной роли")
    @app_commands.choices(role=[
        app_commands.Choice(name="Скаут", value="Скаут"),
        app_commands.Choice(name="Промоутер", value="Промоутер"),
        app_commands.Choice(name="Вербовщик", value="Вербовщик"),
        app_commands.Choice(name="Амбассадор", value="Амбассадор"),
        app_commands.Choice(name="Рекламный Барон", value="Рекламный Барон"),
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def set_referral(interaction: discord.Interaction, user: discord.Member, role: str):
        await _safe_defer(interaction)
        rid = REFERRAL_ROLES.get(role)
        if rid is None:
            await interaction.followup.send(embed=_error_embed("Роль не найдена"), ephemeral=True); return
        ro = interaction.guild.get_role(rid)
        if ro is None:
            await interaction.followup.send(embed=_error_embed("Роль не найдена на сервере"), ephemeral=True); return
        try:
            for rid_old in REFERRAL_ROLES.values():
                r_old = interaction.guild.get_role(rid_old)
                if r_old and r_old in user.roles:
                    await user.remove_roles(r_old, reason="set_referral")
            await user.add_roles(ro, reason="set_referral")
            await interaction.followup.send(f"✅ Роль `{role}` назначена {user.mention}.", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send(embed=_error_embed("Нет прав на назначение роли"), ephemeral=True)

    @set_referral.error
    async def set_referral_error(i: discord.Interaction, e: app_commands.AppCommandError):
        if isinstance(e, app_commands.MissingPermissions):
            try: await i.response.send_message("Недостаточно прав. Требуются права администратора.", ephemeral=True)
            except: await i.followup.send("Недостаточно прав. Требуются права администратора.", ephemeral=True)

    # ---------------------------------------------------------------- #
    #  Sync                                                            #
    # ---------------------------------------------------------------- #
    guild = Object(id=GUILD_ID)
    bot.tree.copy_global_to(guild=guild)
    await bot.tree.sync(guild=guild)
    logger.info("Commands synced to guild %s", GUILD_ID)

# =================================================================== #
#  (f)  MAIN — ЗАПУСК БОТА                                            #
# =================================================================== #

def main() -> None:
    from dotenv import load_dotenv
    load_dotenv()

    global BOT_TOKEN, GOOGLE_SHEETS_CREDS, GOOGLE_SHEETS_URL, GUILD_ID, SHEET_NAME, AUDIT_CHANNEL_ID
    BOT_TOKEN = os.getenv("DISCORD_TOKEN", "")
    GOOGLE_SHEETS_CREDS = os.getenv("GOOGLE_SHEETS_CREDS", "credentials.json")
    GOOGLE_SHEETS_URL = os.getenv("GOOGLE_SHEETS_URL", "")
    GUILD_ID = int(os.getenv("GUILD_ID", "0"))
    SHEET_NAME = os.getenv("SHEET_NAME", "Лист1")
    AUDIT_CHANNEL_ID = int(os.getenv("AUDIT_CHANNEL_ID", "0"))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    logging.getLogger("discord").setLevel(logging.WARNING)

    if not BOT_TOKEN or not GOOGLE_SHEETS_URL:
        raise RuntimeError("Missing required environment variables: DISCORD_TOKEN, GOOGLE_SHEETS_URL")

    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix="/", intents=intents)

    _role_load_referrals()

    @bot.event
    async def on_ready() -> None:
        logger.info("Bot logged in as %s", bot.user)
        await _ensure_form_messages(bot)

    @bot.event
    async def on_message(message: discord.Message) -> None:
        if message.author.bot:
            return
        is_ticket = message.channel.category_id in CATEGORY_CHANNELS.values() if hasattr(message.channel, "category_id") else False
        is_ticket = is_ticket or message.channel.id in CATEGORY_CHANNELS.values()
        if not is_monitored(message.channel.id) and not is_ticket:
            return
        images = [a for a in message.attachments if a.content_type and a.content_type.startswith("image/")]
        if not images:
            return
        await message.add_reaction("⏳")
        prices = _load_prices()
        for att in images:
            try:
                img_bytes = await att.read()
                text = await asyncio.wait_for(_ocr_extract_text(img_bytes), timeout=30.0)
                text = text.strip()
                if not text:
                    continue
                parsed = _ocr_parse_items(text)
                readable, total, unknown = _ocr_cross_reference(parsed, prices)
                items_str = "; ".join(readable)
                result = f"[OCR Result] Items: {items_str} | Total: {_fmt(total)} RUB"
                entry = {"type": "ocr", "timestamp": discord.utils.utcnow().isoformat(),
                         "user_id": message.author.id, "user_name": str(message.author),
                         "message_id": message.id, "filename": att.filename,
                         "items": parsed, "total": total, "unknown": unknown, "raw_text": text}
                await _save_deal_report(message.channel.id, entry)
                await message.channel.send(f"```{result}```")
                if unknown:
                    await message.channel.send(f"⚠️ Не удалось определить цену для: {', '.join(unknown)}. Проверьте базу цен или укажите стоимость вручную.")
            except asyncio.TimeoutError:
                await message.channel.send("⏱ OCR-распознавание превысило таймаут (30 с).")
            except ValueError as exc:
                await message.channel.send(f"⚠️ {exc}")
            except Exception:
                logger.exception("Ошибка OCR при обработке %s", att.filename)
                await message.channel.send("⚠️ Произошла ошибка при OCR-распознавании.")
        await message.remove_reaction("⏳", bot.user)
        await message.add_reaction("✅")

    @bot.event
    async def setup_hook() -> None:
        await setup_commands(bot)

    @bot.tree.error
    async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CommandNotFound):
            try:
                await interaction.response.send_message("❓ Команда не найдена. Попробуйте обновить Discord (Ctrl+R).", ephemeral=True)
            except Exception:
                pass
        elif not isinstance(error, app_commands.MissingPermissions):
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(f"⚠️ Ошибка: {error}", ephemeral=True)
                else:
                    await interaction.followup.send(f"⚠️ Ошибка: {error}", ephemeral=True)
            except Exception:
                pass

    bot.run(BOT_TOKEN)

if __name__ == "__main__":
    main()
