"""Discord-бот для маркетплейса «Клондайк Шёпота».
Автоматизация тикетов: форма заявки, OCR скриншотов, расчёт суммы.
Управление ранговыми и реферальными ролями.
Интеграция с Google Sheets для учёта сделок и профилей пользователей."""

import asyncio
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger("bot")

# =================================================================== #
#  (b)  КОНСТАНТЫ                                                     #
# =================================================================== #

# --- Токен и настройки ---
BOT_TOKEN = os.getenv("DISCORD_TOKEN", "")
GOOGLE_SHEETS_CREDS = os.getenv("GOOGLE_SHEETS_CREDS", "credentials.json")
GOOGLE_SHEETS_URL = os.getenv("GOOGLE_SHEETS_URL", "")
GUILD_ID = int(os.getenv("GUILD_ID", "0"))
SHEET_NAME = os.getenv("SHEET_NAME", "Лист1")
AUDIT_CHANNEL_ID = int(os.getenv("AUDIT_CHANNEL_ID", "0"))

# --- Отслеживаемые каналы (общие) ---
MONITORED_CHANNELS: list[int] = [
    1437410969704730746,
    1430173956492755105,
    1428822870003290112,
    1503656186535350334,
    1283776718435516469,
    1510608354584821931,
    1509663894686269561,
]

# --- Тикет-каналы (категории) ---
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
    "Standard": 0,
    "Premium": 5000,
    "Prestige": 25000,
    "Elite": 100000,
    "Legend": 500000,
}

RANK_ROLES: dict[str, int] = {
    "Standard": 1518324856549277827,
    "Premium": 1518328036137631805,
    "Prestige": 1518328037631066232,
    "Elite": 1518328222939611166,
    "Legend": 1518328324605083698,
}

# --- Реферальные роли ---
REFERRAL_THRESHOLDS: dict[str, int] = {
    "Скаут": 1,
    "Промоутер": 5,
    "Вербовщик": 10,
    "Амбассадор": 25,
    "Рекламный Барон": 100,
}

REFERRAL_ROLES: dict[str, int] = {
    "Скаут": 1518583879672270878,
    "Промоутер": 1518584176054636584,
    "Вербовщик": 1518584268933300274,
    "Амбассадор": 1518584424818671687,
    "Рекламный Барон": 1518584494410563625,
}

# --- Пути к файлам ---
PRICES_FILE = "prices.json"
REFERRALS_FILE = "referrals.json"
DEAL_REPORTS_DIR = "deal_reports"

# =================================================================== #
#  (c)  ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ                                       #
# =================================================================== #

def _fmt(n: float) -> str:
    if n == int(n):
        return str(int(n))
    return f"{n:.2f}".rstrip("0").rstrip(".")

# ------------------------------------------------------------------ #
#  CHANNEL FILTER                                                    #
# ------------------------------------------------------------------ #

def is_monitored(channel_id: int) -> bool:
    return channel_id in MONITORED_CHANNELS

# ------------------------------------------------------------------ #
#  SAFE CALCULATOR  (simpleeval)                                     #
# ------------------------------------------------------------------ #

def safe_calc(expression: str) -> float:
    """Вычислить математическое выражение с помощью simpleeval."""
    import simpleeval
    allowed = {
        "int": int, "float": float, "abs": abs, "round": round,
    }
    result = simpleeval.simple_eval(
        expression,
        functions=allowed,
        names={},
    )
    if not isinstance(result, (int, float)):
        raise ValueError("Результат не является числом")
    return float(result)

# ------------------------------------------------------------------ #
#  GOOGLE SHEETS (обёртка)                                           #
# ------------------------------------------------------------------ #

_gs_client = None

def _gs_connect():
    global _gs_client
    if _gs_client is not None:
        return _gs_client
    import gspread
    from google.oauth2.service_account import Credentials
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(GOOGLE_SHEETS_CREDS, scopes=scopes)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_url(GOOGLE_SHEETS_URL)
    worksheet = spreadsheet.worksheet(SHEET_NAME)
    _gs_client = worksheet
    return worksheet

COL_UNIQUE_NICK = 10
COL_TOTAL_COINS = 11
COL_TOTAL_XP = 12
COL_TOTAL_TURNOVER = 15
COL_REFERRAL_COUNT = 16
COL_BOOSTER = 17
COL_RANK = 18
COL_REFERRAL_ROLE = 19
COL_NICKNAME = 2
COL_BUY = 3
COL_SELL = 4
COL_AMOUNT = 5
COL_REFERRED_BY = 8
DATA_START_ROW = 3

def _gs_parse_float(val) -> float:
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0

def _gs_parse_int(val) -> int:
    if val is None:
        return 0
    if isinstance(val, (int, float)):
        return int(val)
    s = str(val).strip().replace(" ", "").replace(",", ".")
    try:
        return int(float(s))
    except ValueError:
        return 0

@dataclass
class User:
    nickname: str
    coins: float = 0.0
    xp: float = 0.0
    rank: str = ""
    referral_count: int = 0
    referral_role: str = ""
    referred_by: Optional[str] = None
    booster: bool = False
    turnover: float = 0.0

def _gs_find_user(nickname: str) -> Optional[dict]:
    try:
        ws = _gs_connect()
    except Exception:
        return None
    cell = ws.find(nickname, in_column=COL_UNIQUE_NICK)
    if cell is None:
        return None
    vals = ws.row_values(cell.row)
    referred_by = None
    ticket_cells = ws.findall(nickname, in_column=COL_NICKNAME)
    if ticket_cells:
        h_val = ws.cell(ticket_cells[0].row, COL_REFERRED_BY).value
        if h_val and h_val.strip():
            referred_by = h_val.strip()
    turnover_raw = ws.cell(cell.row, COL_TOTAL_TURNOVER).value
    return {
        "nickname": vals[COL_UNIQUE_NICK - 1] if len(vals) >= COL_UNIQUE_NICK else nickname,
        "coins": _gs_parse_float(vals[COL_TOTAL_COINS - 1]) if len(vals) >= COL_TOTAL_COINS else 0.0,
        "xp": _gs_parse_float(vals[COL_TOTAL_XP - 1]) if len(vals) >= COL_TOTAL_XP else 0.0,
        "rank": vals[COL_RANK - 1] if len(vals) >= COL_RANK else "",
        "referral_count": _gs_parse_int(vals[COL_REFERRAL_COUNT - 1]) if len(vals) >= COL_REFERRAL_COUNT else 0,
        "referral_role": vals[COL_REFERRAL_ROLE - 1] if len(vals) >= COL_REFERRAL_ROLE else "",
        "booster": len(vals) >= COL_BOOSTER and vals[COL_BOOSTER - 1] == "TRUE",
        "referred_by": referred_by,
        "turnover": turnover_raw,
    }

def _gs_get_user_profile(nickname: str) -> Optional[User]:
    data = _gs_find_user(nickname)
    if data is None:
        return None
    return User(
        nickname=data["nickname"],
        coins=data["coins"],
        xp=data["xp"],
        rank=data["rank"],
        referral_count=data["referral_count"],
        referral_role=data["referral_role"],
        booster=data["booster"],
        referred_by=data.get("referred_by"),
        turnover=data.get("turnover", 0.0),
    )

# ------------------------------------------------------------------ #
#  РЕФЕРАЛЫ (Google Sheets)                                           #
# ------------------------------------------------------------------ #

def _gs_get_referred_users(nickname: str) -> list[str]:
    try:
        ws = _gs_connect()
    except Exception:
        return []
    cells = ws.findall(nickname, in_column=COL_REFERRED_BY)
    seen = set()
    result = []
    for c in cells:
        nick = ws.cell(c.row, COL_NICKNAME).value
        if nick and nick not in seen:
            seen.add(nick)
            result.append(nick)
    return result

def _gs_set_referred_by(nickname: str, referrer: str) -> None:
    ws = _gs_connect()
    cells = ws.findall(nickname, in_column=COL_NICKNAME)
    for c in cells:
        ws.update_cell(c.row, COL_REFERRED_BY, referrer)

# --- Ранги и бонусы (из таблицы) ---
RANK_XP_THRESHOLDS = [50, 250, 1000, 5000, 10000]
RANK_NAMES_GS = ["🔹 Standard", "🔷 Premium", "💠 Prestige", "💎 Elite", "👑 Legend"]
RANK_BONUSES_GS = [
    "",
    "└ 🎁 🪙 5 Coin",
    "└ 🎁 🪙 10 Coins\n└ ⚡ +5% XP\n└ 📊 Скидка 0.5% / Наценка 0.5%",
    "└ 🎁 🪙 40 Coins\n└ 🔥 +2 Coin за сделку >₽50М\n└ 📊 Скидка 1.5% / Наценка 1%\n└ ⏱ Приоритет",
    "└ 🎁 🪙 100 Coins\n└ 🔥 🪙5 за сделку >₽100М\n└ 📊 Скидка 3% / Наценка 1.5%\n└ ⏱ Приоритет + бронь",
    "└ 🎁 🪙 200 Coins\n└ 💸 🪙10/мес\n└ 📈 +1% от счёта/мес (≤🪙15)\n└ 📊 Скидка 5% / Наценка 2%\n└ 🚀 Без очереди, бронь, спец-заказ",
]
REF_LEVEL_NAMES = ["", "🧭 Скаут", "📣 Промоутер", "🧲 Вербовщик", "📢 Амбассадор", "🎩 Рекламный Барон"]
REF_LEVEL_BONUSES = ["", "└ 🎁 🪙 1 Coin", "└ 🎁 🪙 5 Coins + ⚡ 10 XP", "└ 🎁 🪙 15 Coins\n└ 🛡 Закрепить 1 раз/нед", "└ 🎁 🪙 40 Coins + ⚡ 60 XP\n└ 📉 Скидка 0.5% на бусты", "└ 🎁 🪙 150 Coins\n└ 💸 🪙 0.1 с любой сделки\n└ 🎫 Промокод: -1.5% новичку"]
REF_THRESHOLDS = [1, 5, 10, 25, 100]

def _get_rank_index(xp: float) -> int:
    idx = -1
    for i, t in enumerate(RANK_XP_THRESHOLDS):
        if xp >= t:
            idx = i
        else:
            break
    return idx

def _get_rank_progress(xp: float):
    idx = _get_rank_index(xp)
    if idx == len(RANK_XP_THRESHOLDS) - 1:
        return None
    return (int(xp), RANK_XP_THRESHOLDS[idx + 1], RANK_NAMES_GS[idx + 1])

def _get_rank_bonus(xp: float) -> str:
    idx = _get_rank_index(xp)
    if 0 <= idx < len(RANK_BONUSES_GS):
        return RANK_BONUSES_GS[idx]
    return ""

def _get_referral_level(count: int) -> int:
    level = 0
    for i, t in enumerate(REF_THRESHOLDS):
        if count >= t:
            level = i + 1
        else:
            break
    return level

def _get_next_level_progress(count: int):
    for i, t in enumerate(REF_THRESHOLDS):
        if count < t:
            prev = REF_THRESHOLDS[i - 1] if i > 0 else 0
            return (count - prev, t - prev, REF_LEVEL_NAMES[i])
    return (count, count, "")

# ------------------------------------------------------------------ #
#  EMBED-ПОМОЩНИКИ                                                   #
# ------------------------------------------------------------------ #

def _progress_bar(current: int, total: int, size: int = 10) -> str:
    filled = min(int(current / total * size) if total else 0, size)
    return "🟦" * filled + "⬜" * (size - filled)

def _has(val) -> bool:
    if val is None:
        return False
    if isinstance(val, (int, float)):
        return val != 0
    if isinstance(val, str):
        return val.strip() not in ("", "—", "0")
    return bool(val)

def _profile_embed(user: User, rank_progress=None, rank_bonus: str = "") -> discord.Embed:
    embed = discord.Embed(
        title=f"Профиль — {user.nickname}",
        colour=discord.Colour.blurple(),
    )
    embed.add_field(name="\U0001fa99 Coins", value=_fmt(user.coins))
    embed.add_field(name="\u26a1 XP", value=_fmt(user.xp))
    if _has(user.turnover):
        embed.add_field(name="Общий оборот", value=_fmt(user.turnover))
    if _has(user.rank):
        embed.add_field(name="Ранг", value=user.rank)
    if _has(user.referral_role):
        embed.add_field(name="Реферальная роль", value=user.referral_role)
    if _has(user.referral_count):
        embed.add_field(name="Приглашено", value=_fmt(user.referral_count))
    if rank_bonus:
        embed.add_field(name="Бонус текущего ранга", value=rank_bonus, inline=False)
    if rank_progress:
        current, needed, next_name = rank_progress
        bar = _progress_bar(current, needed)
        embed.add_field(
            name=f"До «{next_name}»",
            value=f"{bar} {_fmt(current)} / {_fmt(needed)} XP",
            inline=False,
        )
    if user.booster:
        embed.add_field(name="\U0001f680 Бустер сервера", value="✅")
    if user.referred_by:
        embed.add_field(name="Ник пригласившего", value=user.referred_by)
    return embed

def _error_embed(msg: str) -> discord.Embed:
    return discord.Embed(title="Ошибка", description=msg, colour=discord.Colour.red())

def _referral_embed(referrer: str) -> discord.Embed:
    return discord.Embed(
        title="Реферал указан",
        description=f"Вы указали, что вас пригласил: `{referrer}`",
        colour=discord.Colour.blurple(),
    )

def _referrals_embed(user: User, referred: list[str], level: int, level_name: str,
                     level_bonus: str, progress, next_bonus: str) -> discord.Embed:
    embed = discord.Embed(
        title=f"Рефералы — {user.nickname}",
        colour=discord.Colour.blurple(),
    )
    text = level_name if level_name else "—"
    if level_bonus:
        text += f"\n{level_bonus}"
    embed.add_field(name="Уровень", value=text, inline=False)
    if referred:
        lines = "\n".join(f"• {u}" for u in referred[:25])
        if len(referred) > 25:
            lines += f"\n… и ещё {_fmt(len(referred) - 25)}"
        embed.add_field(name=f"Приглашено ({_fmt(len(referred))})", value=lines, inline=False)
    else:
        embed.add_field(name="Приглашено", value="Нет приглашённых", inline=False)
    current, needed, next_name = progress
    if next_name:
        bar = _progress_bar(current, needed)
        embed.add_field(name=f"До «{next_name}»", value=f"{bar} {_fmt(current)}/{_fmt(needed)}", inline=False)
        if next_bonus:
            embed.add_field(name="\U0001f381 Награда следующей роли", value=next_bonus, inline=False)
    return embed

# ------------------------------------------------------------------ #
#  DEAL REPORT                                                       #
# ------------------------------------------------------------------ #

async def _save_deal_report(channel_id: int, entry: dict) -> None:
    os.makedirs(DEAL_REPORTS_DIR, exist_ok=True)
    path = os.path.join(DEAL_REPORTS_DIR, f"{channel_id}.json")

    def _sync():
        try:
            with open(path, encoding="utf-8") as f:
                report = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            report = []
        report.append(entry)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

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

def _load_prices() -> dict[str, float]:
    if not os.path.exists(PRICES_FILE):
        logger.warning("Файл цен %s не найден", PRICES_FILE)
        return {}
    try:
        with open(PRICES_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Ошибка загрузки %s: %s", PRICES_FILE, e)
        return {}

async def _ocr_extract_text(image_bytes: bytes) -> str:
    import numpy as np
    from PIL import Image
    import io
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img_array = np.array(img)
    except Exception as exc:
        raise ValueError(f"Не удалось декодировать изображение: {exc}") from exc
    reader = _get_ocr_reader()
    results = await asyncio.to_thread(reader.readtext, img_array)
    texts = [text for _, text, conf in results if conf >= 0.3]
    return "\n".join(texts)

def _ocr_parse_items(text: str) -> list[dict]:
    items = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        m = re.search(r'(.+?)\s*[xхХ×]\s*(\d[\d\s]*)', line)
        if m:
            name = m.group(1).strip()
            qty_str = re.sub(r"\s+", "", m.group(2))
            try:
                qty = int(qty_str)
            except ValueError:
                qty = 1
        else:
            m = re.search(r'(.+?)\s+(\d+)\s*шт', line)
            if m:
                name = m.group(1).strip()
                qty = int(m.group(2))
            else:
                name = line
                qty = 1
        if name:
            items.append({"name": name, "quantity": qty})
    return items

def _ocr_cross_reference(parsed: list[dict], prices: dict[str, float]):
    readable = []
    total = 0.0
    unknown = []
    for item in parsed:
        name = item["name"]
        qty = item["quantity"]
        price = None
        for key, val in prices.items():
            if key.lower() == name.lower():
                price = val
                break
        if price is not None:
            line_total = price * qty
            total += line_total
            readable.append(f"{name} x{qty} = {_fmt(line_total)} ₽")
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
            with open(REFERRALS_FILE, encoding="utf-8") as f:
                _role_referrals = json.load(f)
        except (json.JSONDecodeError, OSError):
            _role_referrals = {}

def _role_save_referrals():
    with open(REFERRALS_FILE, "w", encoding="utf-8") as f:
        json.dump(_role_referrals, f, ensure_ascii=False, indent=2)

def _role_get_referral_count(user_id: int) -> int:
    return _role_referrals.get(str(user_id), 0)

def _role_increment_referral(user_id: int) -> int:
    key = str(user_id)
    count = _role_referrals.get(key, 0) + 1
    _role_referrals[key] = count
    _role_save_referrals()
    return count

def _get_target_rank_name(volume: int | float) -> Optional[str]:
    target = None
    for name, threshold in sorted(RANK_THRESHOLDS.items(), key=lambda x: x[1]):
        if volume >= threshold:
            target = name
    return target

def _get_target_referral_name(count: int) -> Optional[str]:
    target = None
    for name, threshold in sorted(REFERRAL_THRESHOLDS.items(), key=lambda x: x[1]):
        if count >= threshold:
            target = name
    return target

def _get_current_role_from_map(member: discord.Member, role_map: dict[str, int]) -> Optional[discord.Role]:
    member_ids = {r.id for r in member.roles}
    for rid in role_map.values():
        if rid in member_ids:
            guild = member.guild
            return guild.get_role(rid) if guild else None
    return None

def _get_role_name_by_id(role_id: int, role_map: dict[str, int]) -> Optional[str]:
    for name, rid in role_map.items():
        if rid == role_id:
            return name
    return None

async def _sync_role(member: discord.Member, target_name: Optional[str],
                     role_map: dict[str, int], group_label: str) -> Optional[str]:
    target_id = role_map.get(target_name) if target_name else None
    current_role = _get_current_role_from_map(member, role_map)
    if current_role and target_id and current_role.id == target_id:
        return None
    for rid in role_map.values():
        role_obj = member.guild.get_role(rid)
        if role_obj and role_obj in member.roles:
            try:
                await member.remove_roles(role_obj, reason=f"Auto {group_label} sync")
            except (discord.Forbidden, discord.HTTPException):
                pass
    if target_id:
        role_obj = member.guild.get_role(target_id)
        if role_obj is None:
            logger.warning("Роль ID %s (group=%s) не найдена на сервере", target_id, group_label)
            return None
        try:
            await member.add_roles(role_obj, reason=f"Auto {group_label}")
        except discord.Forbidden:
            logger.warning("Нет прав на назначение роли %s пользователю %s", role_obj.name, member)
            return None
        except discord.HTTPException as e:
            logger.error("Ошибка назначения роли %s: %s", role_obj.name, e)
            return None
    old_name = _get_role_name_by_id(current_role.id, role_map) if current_role else "(нет)"
    new_name = target_name or "(нет)"
    logger.info("[ROLE] %s | %s: %s → %s", member.id, group_label, old_name, new_name)
    return target_name

async def _assign_rank_role(member: discord.Member, volume: int | float) -> Optional[str]:
    return await _sync_role(member, _get_target_rank_name(volume), RANK_ROLES, "Rank")

async def _assign_referral_role(member: discord.Member, count: int) -> Optional[str]:
    return await _sync_role(member, _get_target_referral_name(count), REFERRAL_ROLES, "Referral")

# ------------------------------------------------------------------ #
#  AUDIT LOG                                                         #
# ------------------------------------------------------------------ #

async def _audit_log(bot: discord.Client, user: discord.User | discord.Member,
                     command: str, details: dict | str | None = None,
                     success: bool = True) -> None:
    channel = bot.get_channel(AUDIT_CHANNEL_ID)
    if channel is None:
        return
    try:
        labels = {
            "/add": "[ /add ] 📋 Добавление сделки",
            "/profile": "[ /profile ] 👤 Профиль",
            "/refer": "[ /refer ] 🔗 Назначение реферала",
            "/referrals": "[ /referrals ] 👥 Рефералы",
            "/tag": "[ /tag ] 📢 Уведомление",
            "/set_rank": "[ /set_rank ] 🏅 Ранг",
            "/set_referral": "[ /set_referral ] 🔗 Реферальная роль",
        }
        title = labels.get(command, f"📋 {command}")
        embed = discord.Embed(
            title=title,
            colour=discord.Colour.green() if success else discord.Colour.red(),
        )
        embed.add_field(name="\U0001f464 Пользователь", value=f"{user.mention} (`{user.id}`)")
        if details:
            if isinstance(details, dict):
                lines = [f"└ {k}: {v}" for k, v in details.items() if v is not None and v != ""]
                embed.add_field(name="\U0001f4dd Детали лога", value="\n".join(lines), inline=False)
            else:
                embed.add_field(name="\U0001f4dd Детали лога", value=str(details), inline=False)
        embed.set_footer(text="Успешно" if success else "Ошибка")
        await channel.send(embed=embed)
    except Exception as e:
        logger.warning("audit log failed: %s", e)

# ------------------------------------------------------------------ #
#  VIEW & MODAL для формы тикета                                     #
# ------------------------------------------------------------------ #

class TicketFormView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="📝 Заполнить форму",
        style=discord.ButtonStyle.primary,
        custom_id="ticket_form:open",
    )
    async def open_form(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        category = None
        for name, cid in CATEGORY_CHANNELS.items():
            if cid == interaction.channel_id:
                category = name
                break
        if category is None:
            await interaction.response.send_message("Этот канал не является каналом тикета.", ephemeral=True)
            return
        await interaction.response.send_modal(TicketFormModal(category))

class TicketFormModal(discord.ui.Modal):
    def __init__(self, category: str) -> None:
        super().__init__(title=f"Форма — {category}")
        self.category = category
        for field in CATEGORY_FIELDS.get(category, []):
            self.add_item(discord.ui.TextInput(
                label=field["label"],
                custom_id=field["custom_id"],
                required=field["required"],
                style=field.get("style", discord.TextStyle.short),
                placeholder=field.get("placeholder", ""),
                max_length=field.get("max_length", 4000),
            ))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        data = {}
        for child in self.children:
            if isinstance(child, discord.ui.TextInput):
                data[child.custom_id] = child.value
        entry = {
            "type": "form",
            "timestamp": discord.utils.utcnow().isoformat(),
            "user_id": interaction.user.id,
            "user_name": str(interaction.user),
            "category": self.category,
            "data": data,
        }
        await _save_deal_report(interaction.channel_id, entry)
        await interaction.response.send_message("✅ Форма успешно отправлена!", ephemeral=True)

# =================================================================== #
#  (d)  СОБЫТИЯ                                                       #
# =================================================================== #

_initialized = False

async def _ensure_form_messages(bot: commands.Bot) -> None:
    view = None
    for cog in bot.cogs.values():
        if hasattr(cog, "_ticket_view"):
            view = cog._ticket_view
            break
    if view is None:
        view = TicketFormView()
        bot.add_view(view)
    for category, channel_id in CATEGORY_CHANNELS.items():
        channel = bot.get_channel(channel_id)
        if channel is None:
            logger.warning("Канал %s (ID %s) не найден", category, channel_id)
            continue
        try:
            async for msg in channel.history(limit=30):
                if msg.author == bot.user and msg.components:
                    break
            else:
                embed = discord.Embed(
                    title=f"📋 {category}",
                    description=(
                        "Для оформления сделки нажмите кнопку ниже и заполните форму.\n"
                        "Вы также можете прикрепить скриншот — бот автоматически "
                        "распознает предметы и рассчитает сумму."
                    ),
                    colour=discord.Colour.blurple(),
                )
                await channel.send(embed=embed, view=view)
        except Exception:
            pass

# =================================================================== #
#  (e)  SLASH-КОМАНДЫ (в алфавитном порядке)                          #
# =================================================================== #

async def setup_commands(bot: commands.Bot) -> None:
    """Регистрация всех slash-команд на боте."""

    # ---------------------------------------------------------------- #
    #  /add — безопасный калькулятор                                   #
    # ---------------------------------------------------------------- #

    @bot.tree.command(name="add", description="Вычислить математическое выражение и добавить результат к сделке")
    @app_commands.describe(expression="Математическое выражение (например 1500*3+200)")
    async def add_calc(interaction: discord.Interaction, expression: str) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            result = safe_calc(expression)
        except Exception:
            await interaction.followup.send("❌ Недопустимое выражение.", ephemeral=True)
            return

        entry = {
            "type": "calc",
            "timestamp": discord.utils.utcnow().isoformat(),
            "user_id": interaction.user.id,
            "user_name": str(interaction.user),
            "expression": expression,
            "result": result,
        }
        await _save_deal_report(interaction.channel_id, entry)

        await interaction.followup.send(f"✅ Результат: {_fmt(result)}", ephemeral=True)
        try:
            await _audit_log(interaction.client, interaction.user, "/add",
                             {"Выражение": expression, "Результат": _fmt(result)})
        except Exception:
            pass

    # ---------------------------------------------------------------- #
    #  /profile                                                         #
    # ---------------------------------------------------------------- #

    @bot.tree.command(name="profile", description="Ваш ник в таблице")
    @app_commands.describe(nickname="Ваш ник в таблице")
    async def profile(interaction: discord.Interaction, nickname: str) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            user_data = await asyncio.to_thread(_gs_get_user_profile, nickname)
        except Exception as e:
            logger.error("profile error by %s: %s", interaction.user, e)
            await interaction.followup.send(embed=_error_embed("Ошибка при загрузке профиля"), ephemeral=True)
            return
        if user_data is None:
            await interaction.followup.send(embed=_error_embed("Пользователь не найден"), ephemeral=True)
            return
        rank_progress = _get_rank_progress(user_data.xp)
        rank_bonus = _get_rank_bonus(user_data.xp)
        await interaction.followup.send(embed=_profile_embed(user_data, rank_progress, rank_bonus), ephemeral=True)
        try:
            await _audit_log(interaction.client, interaction.user, "/profile", {"Никнейм": nickname})
        except Exception:
            pass

    # ---------------------------------------------------------------- #
    #  /refer                                                           #
    # ---------------------------------------------------------------- #

    @bot.tree.command(name="refer", description="Ник в таблице")
    @app_commands.describe(ник_игрока="Ник в таблице", ник_пригласившего="Кто пригласил")
    @app_commands.checks.has_permissions(administrator=True)
    async def refer(interaction: discord.Interaction, ник_игрока: str, ник_пригласившего: str) -> None:
        await interaction.response.defer(ephemeral=True)
        if ник_игрока.lower() == ник_пригласившего.lower():
            await interaction.followup.send(embed=_error_embed("Нельзя указать самого себя"), ephemeral=True)
            return
        try:
            existing = await asyncio.to_thread(_gs_find_user, ник_игрока)
            if existing and existing.get("referred_by"):
                await interaction.followup.send(embed=_error_embed("Реферал уже указан, изменить нельзя"), ephemeral=True)
                return
            await asyncio.to_thread(_gs_set_referred_by, ник_игрока, ник_пригласившего)
        except Exception as e:
            logger.error("refer error by %s: %s", interaction.user, e)
            await interaction.followup.send(embed=_error_embed("Ошибка при установке реферала"), ephemeral=True)
            return
        await interaction.followup.send(embed=_referral_embed(ник_пригласившего), ephemeral=True)
        try:
            await _audit_log(interaction.client, interaction.user, "/refer",
                             {"Ник игрока": ник_игрока, "Ник пригласившего": ник_пригласившего})
        except Exception:
            pass

    @refer.error
    async def refer_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            text = "Недостаточно прав. Требуются права администратора."
        else:
            text = f"Ошибка: {error}"
        try:
            await interaction.response.send_message(text, ephemeral=True)
        except discord.errors.InteractionResponded:
            await interaction.followup.send(text, ephemeral=True)

    # ---------------------------------------------------------------- #
    #  /referrals                                                       #
    # ---------------------------------------------------------------- #

    @bot.tree.command(name="referrals", description="Ваш ник в таблице")
    @app_commands.describe(nickname="Ваш ник в таблице")
    async def referrals(interaction: discord.Interaction, nickname: str) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            user = await asyncio.to_thread(_gs_get_user_profile, nickname)
        except Exception as e:
            logger.error("referrals error by %s: %s", interaction.user, e)
            await interaction.followup.send(embed=_error_embed("Ошибка при загрузке рефералов"), ephemeral=True)
            return
        if user is None:
            await interaction.followup.send(embed=_error_embed("Пользователь не найден"), ephemeral=True)
            return
        referred = await asyncio.to_thread(_gs_get_referred_users, nickname)
        count = len(referred)
        level = _get_referral_level(count)
        level_name = REF_LEVEL_NAMES[level] if 0 <= level < len(REF_LEVEL_NAMES) else ""
        level_bonus = REF_LEVEL_BONUSES[level] if 0 <= level < len(REF_LEVEL_BONUSES) else ""
        progress = _get_next_level_progress(count)
        next_bonus = REF_LEVEL_BONUSES[level + 1] if level + 1 < len(REF_LEVEL_BONUSES) else ""
        embed = _referrals_embed(user, referred, level, level_name, level_bonus, progress, next_bonus)
        await interaction.followup.send(embed=embed, ephemeral=True)
        try:
            await _audit_log(interaction.client, interaction.user, "/referrals",
                             {"Никнейм": nickname, "Рефералов": count})
        except Exception:
            pass

    # ---------------------------------------------------------------- #
    #  /set_rank                                                        #
    # ---------------------------------------------------------------- #

    @bot.tree.command(name="set_rank", description="Вручную установить ранговую роль участнику")
    @app_commands.describe(user="Вручную установить ранговую роль участнику")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.choices(rank=[
        app_commands.Choice(name="Standard", value="Standard"),
        app_commands.Choice(name="Premium", value="Premium"),
        app_commands.Choice(name="Prestige", value="Prestige"),
        app_commands.Choice(name="Elite", value="Elite"),
        app_commands.Choice(name="Legend", value="Legend"),
    ])
    async def set_rank(interaction: discord.Interaction, user: discord.Member, rank: str) -> None:
        await interaction.response.defer(ephemeral=True)
        result = await _sync_role(user, rank, RANK_ROLES, "Rank")
        if result is None:
            current = _get_current_role_from_map(user, RANK_ROLES)
            if current and current.id == RANK_ROLES.get(rank):
                text = f"Роль `{rank}` уже назначена пользователю {user.mention}."
            else:
                text = "Не удалось назначить роль. Проверьте права бота."
        else:
            text = f"Роль `{result}` назначена пользователю {user.mention}."
        await interaction.followup.send(text, ephemeral=True)
        try:
            await _audit_log(interaction.client, interaction.user, "/set_rank",
                             {"Пользователь": str(user), "Роль": rank})
        except Exception:
            pass

    @set_rank.error
    async def set_rank_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            text = "Недостаточно прав. Требуются права администратора."
        else:
            text = f"Ошибка: {error}"
        try:
            await interaction.response.send_message(text, ephemeral=True)
        except discord.errors.InteractionResponded:
            await interaction.followup.send(text, ephemeral=True)

    # ---------------------------------------------------------------- #
    #  /set_referral                                                    #
    # ---------------------------------------------------------------- #

    @bot.tree.command(name="set_referral", description="Вручную установить реферальную роль участнику")
    @app_commands.describe(user="Вручную установить реферальную роль участнику")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.choices(role=[
        app_commands.Choice(name="Скаут", value="Скаут"),
        app_commands.Choice(name="Промоутер", value="Промоутер"),
        app_commands.Choice(name="Вербовщик", value="Вербовщик"),
        app_commands.Choice(name="Амбассадор", value="Амбассадор"),
        app_commands.Choice(name="Рекламный Барон", value="Рекламный Барон"),
    ])
    async def set_referral(interaction: discord.Interaction, user: discord.Member, role: str) -> None:
        await interaction.response.defer(ephemeral=True)
        result = await _sync_role(user, role, REFERRAL_ROLES, "Referral")
        if result is None:
            current = _get_current_role_from_map(user, REFERRAL_ROLES)
            if current and current.id == REFERRAL_ROLES.get(role):
                text = f"Роль `{role}` уже назначена пользователю {user.mention}."
            else:
                text = "Не удалось назначить роль. Проверьте права бота."
        else:
            text = f"Роль `{result}` назначена пользователю {user.mention}."
        await interaction.followup.send(text, ephemeral=True)
        try:
            await _audit_log(interaction.client, interaction.user, "/set_referral",
                             {"Пользователь": str(user), "Роль": role})
        except Exception:
            pass

    @set_referral.error
    async def set_referral_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            text = "Недостаточно прав. Требуются права администратора."
        else:
            text = f"Ошибка: {error}"
        try:
            await interaction.response.send_message(text, ephemeral=True)
        except discord.errors.InteractionResponded:
            await interaction.followup.send(text, ephemeral=True)

    # ---------------------------------------------------------------- #
    #  /tag                                                             #
    # ---------------------------------------------------------------- #

    @bot.tree.command(name="tag", description="Уведомить участника о тикете в личных сообщениях")
    @app_commands.describe(user="Уведомить участника о тикете в личных сообщениях")
    async def tag(interaction: discord.Interaction, user: discord.User) -> None:
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
        view.add_item(discord.ui.Button(
            label="🔗 Перейти к тикету",
            url=interaction.channel.jump_url,
            style=discord.ButtonStyle.link,
        ))
        try:
            await user.send(embed=embed, view=view)
            await interaction.followup.send(f"✅ Уведомление отправлено пользователю {user.mention}.", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("Не удалось отправить сообщение пользователю.", ephemeral=True)
        try:
            await _audit_log(interaction.client, interaction.user, "/tag", {"Пользователь": str(user)})
        except Exception:
            pass

    # ---------------------------------------------------------------- #
    #  Sync                                                            #
    # ---------------------------------------------------------------- #

    guild = Object(id=GUILD_ID)
    bot.tree.copy_global_to(guild=guild)
    await bot.tree.sync(guild=guild)
    logger.info("Commands synced")

# =================================================================== #
#  (f)  MAIN — ЗАПУСК БОТА                                            #
# =================================================================== #

def main() -> None:
    from dotenv import load_dotenv
    load_dotenv()

    # Перечитываем переменные после загрузки .env
    global BOT_TOKEN, GOOGLE_SHEETS_CREDS, GOOGLE_SHEETS_URL, GUILD_ID, SHEET_NAME, AUDIT_CHANNEL_ID
    BOT_TOKEN = os.getenv("DISCORD_TOKEN", "")
    GOOGLE_SHEETS_CREDS = os.getenv("GOOGLE_SHEETS_CREDS", "credentials.json")
    GOOGLE_SHEETS_URL = os.getenv("GOOGLE_SHEETS_URL", "")
    GUILD_ID = int(os.getenv("GUILD_ID", "0"))
    SHEET_NAME = os.getenv("SHEET_NAME", "Лист1")
    AUDIT_CHANNEL_ID = int(os.getenv("AUDIT_CHANNEL_ID", "0"))

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root = logging.getLogger("bot")
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    logging.getLogger("discord").setLevel(logging.WARNING)

    if not BOT_TOKEN or not GOOGLE_SHEETS_URL:
        raise RuntimeError("Missing required environment variables: DISCORD_TOKEN, GOOGLE_SHEETS_URL")

    intents = discord.Intents.default()
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

        # Проверка: канал входит в список отслеживаемых ИЛИ в тикет-каналы
        if not is_monitored(message.channel.id) and message.channel.id not in CATEGORY_CHANNELS.values():
            return

        # OCR-обработка изображений в тикет-каналах
        if message.channel.id in CATEGORY_CHANNELS.values():
            images = [
                a for a in message.attachments
                if a.content_type and a.content_type.startswith("image/")
            ]
            if not images:
                return
            await message.add_reaction("⏳")
            prices = _load_prices()
            for attachment in images:
                try:
                    img_bytes = await attachment.read()
                    text = await asyncio.wait_for(
                        _ocr_extract_text(img_bytes),
                        timeout=30.0,
                    )
                    text = text.strip()
                    if not text:
                        continue
                    parsed = _ocr_parse_items(text)
                    readable, total, unknown = _ocr_cross_reference(parsed, prices)
                    items_str = "; ".join(readable)
                    result = f"[OCR Result] Items: {items_str} | Total: {_fmt(total)} RUB"
                    entry = {
                        "type": "ocr",
                        "timestamp": discord.utils.utcnow().isoformat(),
                        "user_id": message.author.id,
                        "user_name": str(message.author),
                        "message_id": message.id,
                        "filename": attachment.filename,
                        "items": parsed,
                        "total": total,
                        "unknown": unknown,
                        "raw_text": text,
                    }
                    await _save_deal_report(message.channel.id, entry)
                    await message.channel.send(f"```{result}```")
                    if unknown:
                        await message.channel.send(
                            f"⚠️ Не удалось определить цену для: {', '.join(unknown)}. "
                            "Проверьте базу цен или укажите стоимость вручную.",
                        )
                except asyncio.TimeoutError:
                    await message.channel.send("⏱ OCR-распознавание превысило таймаут (30 с).")
                except ValueError as exc:
                    await message.channel.send(f"⚠️ {exc}")
                except Exception:
                    logger.exception("Ошибка OCR при обработке %s", attachment.filename)
                    await message.channel.send("⚠️ Произошла ошибка при OCR-распознавании.")
            await message.remove_reaction("⏳", bot.user)
            await message.add_reaction("✅")

    @bot.event
    async def setup_hook() -> None:
        await setup_commands(bot)

    bot.run(BOT_TOKEN)


if __name__ == "__main__":
    main()
