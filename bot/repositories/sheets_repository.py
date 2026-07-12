import time
import functools
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials
from gspread.exceptions import APIError

from bot.config.constants import (
    COL_NICKNAME, COL_BUY, COL_SELL, COL_AMOUNT,
    COL_REFERRED_BY, COL_UNIQUE_NICK,
    COL_TOTAL_COINS, COL_TOTAL_XP, COL_RANK,
    COL_REFERRAL_COUNT, COL_REFERRAL_ROLE, COL_BOOSTER,
    COL_TOTAL_TURNOVER,
    DATA_START_ROW, MAX_RETRIES, RETRY_MIN_WAIT,
)


def _retry(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        last_exc = None
        for attempt in range(MAX_RETRIES):
            try:
                return func(*args, **kwargs)
            except APIError as e:
                last_exc = e
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_MIN_WAIT * (2 ** attempt))
        raise last_exc
    return wrapper


class SheetsRepository:

    def __init__(self, sheet_name: str, creds_path: str, sheet_url: str) -> None:
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
        self._client = gspread.authorize(creds)
        self._spreadsheet = self._client.open_by_url(sheet_url)
        self._sheet = self._spreadsheet.worksheet(sheet_name)

    @staticmethod
    def _parse_float(value) -> float:
        if value is None:
            return 0.0
        if isinstance(value, (int, float)):
            return float(value)
        s = str(value).strip()
        if not s:
            return 0.0
        cleaned = s.replace(" ", "").replace(",", ".")
        try:
            return float(cleaned)
        except ValueError:
            return 0.0

    @staticmethod
    def _parse_int(value) -> int:
        if value is None:
            return 0
        if isinstance(value, (int, float)):
            return int(value)
        s = str(value).strip()
        if not s:
            return 0
        cleaned = s.replace(" ", "").replace(",", ".")
        try:
            return int(float(cleaned))
        except ValueError:
            return 0

    @_retry
    def find_user(self, nickname: str) -> Optional[dict]:
        """Return user stats by nickname from the user database (J-S columns).
        Finds the user in column J (Уникальный ник) and reads data
        from J-S columns of that same row."""
        cell = self._sheet.find(nickname, in_column=COL_UNIQUE_NICK)
        if cell is None:
            return None

        # Read all data from the J-row (user database section)
        vals = self._sheet.row_values(cell.row)

        # Read referred_by from ticket rows (column H)
        referred_by = None
        ticket_cells = self._sheet.findall(nickname, in_column=COL_NICKNAME)
        if ticket_cells:
            h_val = self._sheet.cell(ticket_cells[0].row, COL_REFERRED_BY).value
            if h_val and h_val.strip():
                referred_by = h_val.strip()

        # Read turnover from column O of the J-row
        turnover_raw = self._sheet.cell(cell.row, COL_TOTAL_TURNOVER).value
        turnover = self._parse_float(turnover_raw)

        return {
            "nickname": vals[COL_UNIQUE_NICK - 1] if len(vals) >= COL_UNIQUE_NICK else nickname,
            "coins": self._parse_float(vals[COL_TOTAL_COINS - 1]) if len(vals) >= COL_TOTAL_COINS else 0.0,
            "xp": self._parse_float(vals[COL_TOTAL_XP - 1]) if len(vals) >= COL_TOTAL_XP else 0.0,
            "rank": vals[COL_RANK - 1] if len(vals) >= COL_RANK else "",
            "referral_count": self._parse_int(vals[COL_REFERRAL_COUNT - 1]) if len(vals) >= COL_REFERRAL_COUNT else 0,
            "referral_role": vals[COL_REFERRAL_ROLE - 1] if len(vals) >= COL_REFERRAL_ROLE else "",
            "booster": len(vals) >= COL_BOOSTER and vals[COL_BOOSTER - 1] == "TRUE",
            "referred_by": referred_by,
            "turnover": turnover,
        }

    def _last_row(self) -> int:
        cells = self._sheet.range(f"B1:B")
        last = 1
        for cell in cells:
            if cell.value:
                last = cell.row
        return last

    def _copy_formulas_to_new_row(self, index: int) -> None:
        """Copy formulas from row above (index-1) to the new row (index)."""
        if index <= 2:
            return
        sheet_id = self._sheet.id
        src_start = index - 2
        src_end = index - 1
        dst_start = index - 1
        dst_end = index

        self._spreadsheet.batch_update({
            "requests": [
                {
                    "copyPaste": {
                        "source": {
                            "sheetId": sheet_id,
                            "startRowIndex": src_start,
                            "endRowIndex": src_end,
                            "startColumnIndex": 10,  # K (0-based)
                            "endColumnIndex": 21,    # U (exclusive)
                        },
                        "destination": {
                            "sheetId": sheet_id,
                            "startRowIndex": dst_start,
                            "endRowIndex": dst_end,
                            "startColumnIndex": 10,
                            "endColumnIndex": 21,
                        },
                        "pasteType": "PASTE_FORMULA",
                    }
                },
            ]
        })

    @_retry
    def ensure_user(self, nickname: str) -> bool:
        """Check if nickname exists in column B (ticket section). Return True if created."""
        cell = self._sheet.find(nickname, in_column=COL_NICKNAME)
        if cell is not None:
            return False

        index = self._last_row() + 1
        self._sheet.insert_row(["", nickname], index)
        self._copy_formulas_to_new_row(index)
        return True

    @_retry
    def append_transaction(
        self, nickname: str, tx_type: str, amount: float,
        referrer: str | None = None,
    ) -> None:
        """Append a transaction row, copying formulas from the row above."""
        index = self._last_row() + 1
        row = ["", nickname, True, False, amount, "", "", referrer or ""]

        if tx_type == "buy":
            self._sheet.insert_row(row, index)
        else:
            row[2], row[3] = False, True
            self._sheet.insert_row(row, index)

        self._copy_formulas_to_new_row(index)

    @_retry
    def set_referred_by(self, nickname: str, referrer: str) -> None:
        """Update column H (Пришел от) for every transaction row of this user."""
        cells = self._sheet.findall(nickname, in_column=COL_NICKNAME)
        for cell in cells:
            self._sheet.update_cell(cell.row, COL_REFERRED_BY, referrer)

    @_retry
    def find_referrals(self, nickname: str) -> list[dict]:
        """Find all users who have this nickname in column H (Пришел от)."""
        cells = self._sheet.findall(nickname, in_column=COL_REFERRED_BY)

        result = []
        for cell in cells:
            result.append({
                "row": cell.row,
                "nickname": self._sheet.cell(cell.row, COL_NICKNAME).value,
            })
        return result

    @_retry
    def get_user_nicknames(self) -> list[str]:
        """Return all unique nicknames from column J (user database)."""
        return self._sheet.col_values(COL_UNIQUE_NICK)[DATA_START_ROW - 1:]

    @_retry
    def user_has_referral(self, nickname: str) -> bool:
        """Check if user already has a referrer set in column H."""
        cells = self._sheet.findall(nickname, in_column=COL_NICKNAME)
        for cell in cells:
            h_value = self._sheet.cell(cell.row, COL_REFERRED_BY).value
            if h_value and h_value.strip():
                return True
        return False
