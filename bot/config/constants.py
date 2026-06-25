from typing import Final

# 1-based column indices (gspread format)
COL_NICKNAME: Final[int] = 2       # B
COL_BUY: Final[int] = 3            # C
COL_SELL: Final[int] = 4           # D
COL_AMOUNT: Final[int] = 5         # E
COL_COINS_FORMULA: Final[int] = 6  # F  (formula)
COL_XP_FORMULA: Final[int] = 7     # G  (formula)
COL_REFERRED_BY: Final[int] = 8    # H
# I = 9 — пустой разделитель
COL_UNIQUE_NICK: Final[int] = 10   # J  (=UNIQUE)
COL_TOTAL_COINS: Final[int] = 11   # K  (formula)
COL_TOTAL_XP: Final[int] = 12      # L  (formula)
# M = 13 — Оборот покупок (formula, not used)
# N = 14 — Оборот продаж (formula, not used)
COL_TOTAL_TURNOVER: Final[int] = 15  # O  (formula)
COL_REFERRAL_COUNT: Final[int] = 16 # P  (formula)
COL_BOOSTER: Final[int] = 17       # Q
COL_RANK: Final[int] = 18          # R  (formula)
COL_REFERRAL_ROLE: Final[int] = 19 # S  (formula)
# T = 20 — пустой разделитель
# U = 21 — Трата Coins (formula, not used)

DATA_START_ROW: Final[int] = 3

MAX_RETRIES: Final[int] = 3
RETRY_MIN_WAIT: Final[float] = 1.0
RETRY_MAX_WAIT: Final[float] = 10.0
