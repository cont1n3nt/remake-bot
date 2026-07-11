from typing import Final

# 1-based column indices (gspread format)
# — Секция тикетов (B-H) —
COL_NICKNAME: Final[int] = 1       # A — Ник
COL_BUY: Final[int] = 2            # B — Покупка
COL_SELL: Final[int] = 3           # C — Продажа
COL_AMOUNT: Final[int] = 4         # D — Сумма
COL_COINS: Final[int] = 5          # E — Coins (формула, не трогать)
COL_XP: Final[int] = 6             # F — XP (формула, не трогать)
COL_REFERRED_BY: Final[int] = 7    # G — Пришел от
COL_TICKET_EMPTY: Final[int] = 8   # H — пустой разделитель
# I = 9 — пустой разделитель
# — Секция базы пользователей (J-S) —
COL_UNIQUE_NICK: Final[int] = 10   # J — Уникальный ник
COL_TOTAL_COINS: Final[int] = 11   # K — Всего Coins (formula)
COL_TOTAL_XP: Final[int] = 12      # L — Всего XP (formula)
# M = 13 — Оборот покупок (formula, not used)
# N = 14 — Оборот продаж (formula, not used)
COL_TOTAL_TURNOVER: Final[int] = 15  # O — Общий оборот (formula)
COL_REFERRAL_COUNT: Final[int] = 16 # P — Рефералы (formula)
COL_BOOSTER: Final[int] = 17       # Q — Бустер сервера
COL_RANK: Final[int] = 18          # R — Ранг (formula)
COL_REFERRAL_ROLE: Final[int] = 19 # S — Роль реферала (formula)
# T = 20 — пустой разделитель
# U = 21 — Ник
# V = 22 — Трата Coins (formula, not used)

DATA_START_ROW: Final[int] = 3

MAX_RETRIES: Final[int] = 3
RETRY_MIN_WAIT: Final[float] = 1.0
RETRY_MAX_WAIT: Final[float] = 10.0
