from typing import Final

# 1-based column indices (gspread format)
# — Секция тикетов —
COL_NICKNAME: Final[int] = 2       # B — Ник
COL_BUY: Final[int] = 3            # C — Покупка (чекбокс)
COL_SELL: Final[int] = 4           # D — Продажа (чекбокс)
COL_AMOUNT: Final[int] = 5         # E — Сумма
COL_COINS_FORMULA: Final[int] = 6  # F — Coins (формула листа, не трогать)
COL_XP_FORMULA: Final[int] = 7     # G — XP (формула листа, не трогать)
COL_REFERRED_BY: Final[int] = 8    # H — Пришел от
# I = 9 — пустой разделитель
# — Секция базы пользователей (J-S) —
COL_UNIQUE_NICK: Final[int] = 10   # J — Уникальный ник
COL_TOTAL_COINS: Final[int] = 11   # K — Всего Coins (formula)
COL_TOTAL_XP: Final[int] = 12      # L — Всего XP (formula)
# M = 13 — Оборот покупок (formula)
# N = 14 — Оборот продаж (formula)
COL_TOTAL_TURNOVER: Final[int] = 15  # O — Общий оборот (formula)
COL_REFERRAL_COUNT: Final[int] = 16 # P — Рефералы (formula)
COL_BOOSTER: Final[int] = 17       # Q — Бустер сервера (ручной ввод)
COL_RANK: Final[int] = 18          # R — Ранг (formula)
COL_REFERRAL_ROLE: Final[int] = 19 # S — Роль реферала (formula)
# T = 20 — пустой разделитель
# U = 21 — Ник (магазин)
# V = 22 — Трата Coins (formula)

# — База предметов DataBase (AA:AG) —
COL_DB_ID: Final[int] = 27         # AA — ID
COL_DB_NAME: Final[int] = 28       # AB — Название предмета
COL_DB_CATEGORY: Final[int] = 29   # AC — Категория (resource/boost)
COL_DB_PRICE_BUY: Final[int] = 30  # AD — Цена скупки
COL_DB_PRICE_SELL: Final[int] = 31 # AE — Цена продажи
COL_DB_EMOJI: Final[int] = 32      # AF — Эмодзи
COL_DB_UPDATED: Final[int] = 33    # AG — Дата обновления

DATA_START_ROW: Final[int] = 3

MAX_RETRIES: Final[int] = 3
RETRY_MIN_WAIT: Final[float] = 1.0
RETRY_MAX_WAIT: Final[float] = 10.0

# Имена листов для синхронизации цен (/sync_prices)
SYNC_SHEET_SKUP: Final[str] = "Мейн скуп"
SYNC_SHEET_SKUP_BOOST: Final[str] = "Скуп бустов"
SYNC_SHEET_BOOST_SALE: Final[str] = "БУСТЫ"

# Пары столбцов (колонка названия, колонка цены) для синхронизации
# Формат: (name_col, price_col) — 1-based
SYNC_COLUMN_PAIRS_FULL = [
    (3, 4),   # C → D
    (10, 11), # J → K
    (17, 18), # Q → R
    (24, 25), # X → Y
    (31, 32), # AE → AF
    (38, 39), # AL → AM
    (45, 46), # AS → AT
]
SYNC_COLUMN_PAIRS_HALF = [
    (3, 4),   # C → D
    (10, 11), # J → K
    (17, 18), # Q → R
    (24, 25), # X → Y
]

SYNC_MAX_ROWS_SKUP: Final[int] = 31       # Мейн скуп
SYNC_MAX_ROWS_BOOST: Final[int] = 9       # Продажа бустов
SYNC_MAX_ROWS_SKUP_BOOST: Final[int] = 9   # Скуп бустов

# — Отслеживаемые каналы —
MONITORED_CHANNELS: Final[list[int]] = [
    1437410969704730746,
    1430173956492755105,
    1428822870003290112,
    1503656186535350334,
    1283776718435516469,
    1510608354584821931,
    1509663894686269561,
]

# — Тикет-каналы —
CATEGORY_CHANNELS: dict[str, int] = {
    "Продажа предметов": 1475149130748657841,
    "Продажа бустов":   1503802805801058336,
    "Заказ бустов":     1479228622014251049,
}

# — Ранговые роли —
RANK_THRESHOLDS: dict[str, int] = {
    "Standard": 0, "Premium": 5000, "Prestige": 25000, "Elite": 100000, "Legend": 500000,
}
RANK_ROLES: dict[str, int] = {
    "Standard": 1518324856549277827, "Premium": 1518328036137631805,
    "Prestige": 1518328037631066232, "Elite": 1518328222939611166,
    "Legend": 1518328324605083698,
}

# — Реферальные роли —
REFERRAL_THRESHOLDS: dict[str, int] = {
    "Скаут": 1, "Промоутер": 5, "Вербовщик": 10, "Амбассадор": 25, "Рекламный Барон": 100,
}
REFERRAL_ROLES: dict[str, int] = {
    "Скаут": 1518583879672270878, "Промоутер": 1518584176054636584,
    "Вербовщик": 1518584268933300274, "Амбассадор": 1518584424818671687,
    "Рекламный Барон": 1518584494410563625,
}

# — Эмодзи-метки рангов/рефералов (REFACTORING_PLAN.md, Этап D.4) —
# Именно в таком виде формула Google Sheets возвращает значения в колонках
# R (ранг) и S (реферальная роль): "эмодзи + пробел + имя". Пользователь
# сверил вручную по живой таблице 2026-07-30: подтверждены "🔹 Standard",
# "🔷 Premium", "🧭 Скаут", "📣 Промоутер" — совпадают. Остальные пять
# значений (Prestige/Elite/Legend, Вербовщик/Амбассадор/Рекламный Барон)
# приняты по аналогии формата (та же схема "эмодзи + имя", тот же список
# эмодзи, что уже был захардкожен в bot/cogs/transactions.py до этого
# этапа) — НЕ сверены построчно с таблицей. Если авто-выдача ролей после
# сделки перестанет находить упоминание роли для одного из непроверенных
# уровней — начните разбор именно с этих пяти строк.
RANK_EMOJI_LABELS: dict[str, str] = {
    "Standard": "🔹 Standard",
    "Premium": "🔷 Premium",
    "Prestige": "💠 Prestige",
    "Elite": "💎 Elite",
    "Legend": "👑 Legend",
}
REFERRAL_EMOJI_LABELS: dict[str, str] = {
    "Скаут": "🧭 Скаут",
    "Промоутер": "📣 Промоутер",
    "Вербовщик": "🧲 Вербовщик",
    "Амбассадор": "📢 Амбассадор",
    "Рекламный Барон": "🎩 Рекламный Барон",
}

RANK_ROLES_BY_LABEL: dict[str, int] = {
    label: RANK_ROLES[name] for name, label in RANK_EMOJI_LABELS.items()
}
REFERRAL_ROLES_BY_LABEL: dict[str, int] = {
    label: REFERRAL_ROLES[name] for name, label in REFERRAL_EMOJI_LABELS.items()
}
