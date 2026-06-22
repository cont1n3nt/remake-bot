from typing import Final

# 1-based column indices (gspread format)
COL_NICKNAME: Final[int] = 2       # B
COL_BUY: Final[int] = 3            # C
COL_SELL: Final[int] = 4           # D
COL_AMOUNT: Final[int] = 5         # E
COL_COINS_FORMULA: Final[int] = 6  # F  (auto-calculated)
COL_XP_FORMULA: Final[int] = 7     # G  (auto-calculated)
COL_REFERRED_BY: Final[int] = 8    # H
# I = 9 — пустой разделитель
COL_UNIQUE_NICK: Final[int] = 10   # J  (formula =UNIQUE)
COL_TOTAL_COINS: Final[int] = 11   # K  (formula)
COL_TOTAL_XP: Final[int] = 12      # L  (formula)
COL_RANK: Final[int] = 13          # M  (formula)
COL_REFERRAL_COUNT: Final[int] = 14 # N  (formula)
COL_REFERRAL_ROLE: Final[int] = 15 # O  (formula)
COL_BOOSTER: Final[int] = 16       # P

DATA_START_ROW: Final[int] = 3

MAX_RETRIES: Final[int] = 3
RETRY_MIN_WAIT: Final[float] = 1.0
RETRY_MAX_WAIT: Final[float] = 10.0
