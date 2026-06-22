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
    MAX_RETRIES, RETRY_MIN_WAIT,
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
    def _parse_float(value: str) -> float:
        if not value or value.strip() == "":
            return 0.0
        cleaned = value.replace(" ", "").replace(",", ".")
        try:
            return float(cleaned)
        except ValueError:
            return 0.0

    @staticmethod
    def _parse_int(value: str) -> int:
        if not value or value.strip() == "":
            return 0
        cleaned = value.replace(" ", "").replace(",", ".")
        try:
            return int(float(cleaned))
        except ValueError:
            return 0

    @_retry
    def find_user(self, nickname: str) -> Optional[dict]:
        """Search unique nickname in column J, return stats dict or None."""
        cell = self._sheet.find(nickname, in_column=COL_UNIQUE_NICK)
        if cell is None:
            return None

        vals = self._sheet.row_values(cell.row)

        return {
            "nickname": vals[COL_UNIQUE_NICK - 1],
            "coins": self._parse_float(vals[COL_TOTAL_COINS - 1]),
            "xp": self._parse_float(vals[COL_TOTAL_XP - 1]),
            "rank": vals[COL_RANK - 1] if len(vals) >= COL_RANK else "",
            "referral_count": self._parse_int(vals[COL_REFERRAL_COUNT - 1]),
            "referral_role": vals[COL_REFERRAL_ROLE - 1] if len(vals) >= COL_REFERRAL_ROLE else "",
            "booster": len(vals) >= COL_BOOSTER and vals[COL_BOOSTER - 1] == "TRUE",
        }

    def _last_row(self) -> int:
        return len(self._sheet.get_all_values())

    def _insert_with_formulas(
        self, target_row: int, source_row: int, col_values: dict[int, object]
    ) -> None:
        """Insert a row, copy formulas from source, write values to specific columns.

        Args:
            target_row: 1-indexed row to insert at (existing rows shift down).
            source_row: 1-indexed row to copy formulas from.
            col_values: {0-based column_index: value} — written after copying.
        """
        sheet_id = self._sheet.id

        requests = [
            {
                "insertDimension": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "ROWS",
                        "startIndex": target_row - 1,
                        "endIndex": target_row,
                    },
                    "inheritFromBefore": True,
                }
            },
            {
                "copyPaste": {
                    "source": {
                        "sheetId": sheet_id,
                        "startRowIndex": source_row - 1,
                        "endRowIndex": source_row,
                        "startColumnIndex": 0,
                        "endColumnIndex": 12,
                    },
                    "destination": {
                        "sheetId": sheet_id,
                        "startRowIndex": target_row - 1,
                        "endRowIndex": target_row,
                        "startColumnIndex": 0,
                        "endColumnIndex": 12,
                    },
                    "pasteType": "PASTE_FORMULA",
                }
            },
        ]

        if col_values:
            cells = []
            for col in range(12):
                if col in col_values:
                    val = col_values[col]
                    if isinstance(val, bool):
                        cells.append({"userEnteredValue": {"boolValue": val}})
                    elif isinstance(val, (int, float)):
                        cells.append({"userEnteredValue": {"numberValue": val}})
                    else:
                        cells.append({"userEnteredValue": {"stringValue": str(val)}})
            if cells:
                requests.append({
                    "updateCells": {
                        "rows": [{"values": cells}],
                        "fields": "userEnteredValue",
                        "start": {
                            "sheetId": sheet_id,
                            "rowIndex": target_row - 1,
                            "columnIndex": 0,
                        },
                    }
                })

        self._spreadsheet.batch_update({"requests": requests})

    @_retry
    def ensure_user(self, nickname: str) -> bool:
        """Check if nickname exists. Return True if created."""
        cell = self._sheet.find(nickname, in_column=COL_NICKNAME)
        if cell is not None:
            return False

        last = self._last_row()
        self._insert_with_formulas(last + 1, last, {1: nickname})
        return True

    @_retry
    def append_transaction(self, nickname: str, tx_type: str, amount: float) -> None:
        """Append a transaction row with formulas + checkboxes."""
        last = self._last_row()
        is_buy = tx_type == "buy"

        self._insert_with_formulas(
            last + 1,
            last,
            {
                1: nickname,
                2: is_buy,
                3: not is_buy,
                4: amount,
            },
        )

    @_retry
    def set_referred_by(self, nickname: str, referrer: str) -> None:
        """Update column H for every transaction row of this user."""
        cells = self._sheet.findall(nickname, in_column=COL_NICKNAME)
        for cell in cells:
            self._sheet.update_cell(cell.row, COL_REFERRED_BY, referrer)

    @_retry
    def find_referrals(self, nickname: str) -> list[dict]:
        """Find all users who have this nickname in column H."""
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
        """Return all unique nicknames from column B (raw data)."""
        return self._sheet.col_values(COL_NICKNAME)[DATA_START_ROW - 1:]

    @_retry
    def user_has_referral(self, nickname: str) -> bool:
        """Check if user already has a referrer set in column H."""
        cells = self._sheet.findall(nickname, in_column=COL_NICKNAME)
        for cell in cells:
            h_value = self._sheet.cell(cell.row, COL_REFERRED_BY).value
            if h_value and h_value.strip():
                return True
        return False
