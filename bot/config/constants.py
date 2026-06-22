from typing import Final

SHEET_USERS: Final[str] = "Users"
SHEET_TRANSACTIONS: Final[str] = "Transactions"

COL_USERS_DISCORD_ID: Final[int] = 1
COL_USERS_NICKNAME: Final[int] = 2
COL_USERS_COINS: Final[int] = 3
COL_USERS_XP: Final[int] = 4
COL_USERS_LEVEL: Final[int] = 5
COL_USERS_REFERRAL_CODE: Final[int] = 6
COL_USERS_REFERRED_BY: Final[int] = 7
COL_USERS_REFERRAL_COUNT: Final[int] = 8
COL_USERS_CREATED_AT: Final[int] = 9

COL_TX_TIMESTAMP: Final[int] = 1
COL_TX_DISCORD_ID: Final[int] = 2
COL_TX_NICKNAME: Final[int] = 3
COL_TX_TYPE: Final[int] = 4
COL_TX_AMOUNT: Final[int] = 5
COL_TX_RAW_LOG: Final[int] = 6

MAX_RETRIES: Final[int] = 3
RETRY_MIN_WAIT: Final[float] = 1.0
RETRY_MAX_WAIT: Final[float] = 10.0

REFERRAL_CODE_MIN_LENGTH: Final[int] = 4
REFERRAL_CODE_MAX_LENGTH: Final[int] = 16

DISCORD_ID_MIN_LENGTH: Final[int] = 17
DISCORD_ID_MAX_LENGTH: Final[int] = 19
