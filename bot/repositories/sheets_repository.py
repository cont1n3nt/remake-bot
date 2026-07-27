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
    COL_DB_ID, COL_DB_NAME, COL_DB_CATEGORY,
    COL_DB_PRICE_BUY, COL_DB_PRICE_SELL, COL_DB_EMOJI, COL_DB_UPDATED,
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

    def _ensure_jrow(self, nickname: str) -> None:
        """Copy K-U formulas to the UNIQUE row for this nickname if empty."""
        try:
            cell = self._sheet.find(nickname, in_column=COL_UNIQUE_NICK)
            if cell is None:
                return
            k_raw = self._sheet.cell(cell.row, COL_TOTAL_COINS).value
            if k_raw and str(k_raw).strip():
                return
            src_row = None
            for b_cell in self._sheet.findall(nickname, in_column=COL_NICKNAME):
                br = self._sheet.row_values(b_cell.row)
                if len(br) >= COL_TOTAL_COINS and br[COL_TOTAL_COINS - 1].strip():
                    src_row = b_cell.row
                    break
            if src_row is None or src_row == cell.row:
                return
            sid = self._sheet.id
            self._spreadsheet.batch_update({
                "requests": [{
                    "copyPaste": {
                        "source": {
                            "sheetId": sid,
                            "startRowIndex": src_row - 1,
                            "endRowIndex": src_row,
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
            })
        except Exception:
            pass

    @_retry
    def ensure_user(self, nickname: str) -> bool:
        """Check if nickname exists in column B (ticket section). Return True if created."""
        cell = self._sheet.find(nickname, in_column=COL_NICKNAME)
        if cell is not None:
            return False

        index = self._last_row() + 1
        self._sheet.insert_row(["", nickname], index)
        self._copy_formulas_to_new_row(index)
        self._ensure_jrow(nickname)
        return True

    @_retry
    def append_transaction(
        self, nickname: str, tx_type: str, amount: float,
        referrer: str | None = None,
    ) -> None:
        """Append a transaction row, copying formulas from the row above."""
        from datetime import datetime, timezone, timedelta
        now = (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%d.%m.%y %H:%M")
        index = self._last_row() + 1
        row = [now, nickname, True, False, amount, "", "", referrer or ""]

        if tx_type == "buy":
            self._sheet.insert_row(row, index)
        else:
            row[2], row[3] = False, True
            self._sheet.insert_row(row, index)

        self._copy_formulas_to_new_row(index)
        self._ensure_jrow(nickname)
        if referrer:
            self._ensure_jrow(referrer)

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

    # ------------------------------------------------------------------ #
    #  База предметов (DataBase AA:AG)                                    #
    # ------------------------------------------------------------------ #

    @_retry
    def get_all_items(self) -> list[dict]:
        """Return all items from the DataBase section (AA:AG columns)."""
        vals = self._sheet.get(f"AA{DATA_START_ROW}:AG{self._sheet.row_count}")
        items = []
        for row in vals:
            if len(row) < 2 or not str(row[1]).strip():
                continue
            try:
                items.append({
                    "id": int(row[0]) if row[0] else 0,
                    "name": str(row[1]).strip(),
                    "category": str(row[2]).strip() if len(row) > 2 else "",
                    "price_buy": self._parse_float(row[3]) if len(row) > 3 else None,
                    "price_sell": self._parse_float(row[4]) if len(row) > 4 else None,
                    "emoji": str(row[5]).strip() if len(row) > 5 else "",
                    "updated_at": str(row[6]).strip() if len(row) > 6 else "",
                })
            except (ValueError, IndexError):
                continue
        return items

    @_retry
    def find_item(self, name: str) -> Optional[dict]:
        """Find item by name in the DataBase (column AB)."""
        cell = self._sheet.find(name, in_column=COL_DB_NAME)
        if cell is None:
            return None
        row = self._sheet.row_values(cell.row)
        return {
            "id": self._parse_int(row[COL_DB_ID - 1]) if len(row) >= COL_DB_ID else 0,
            "name": str(row[COL_DB_NAME - 1]).strip() if len(row) >= COL_DB_NAME else name,
            "category": str(row[COL_DB_CATEGORY - 1]).strip() if len(row) >= COL_DB_CATEGORY else "",
            "price_buy": self._parse_float(row[COL_DB_PRICE_BUY - 1]) if len(row) >= COL_DB_PRICE_BUY else None,
            "price_sell": self._parse_float(row[COL_DB_PRICE_SELL - 1]) if len(row) >= COL_DB_PRICE_SELL else None,
            "emoji": str(row[COL_DB_EMOJI - 1]).strip() if len(row) >= COL_DB_EMOJI else "",
            "updated_at": str(row[COL_DB_UPDATED - 1]).strip() if len(row) >= COL_DB_UPDATED else "",
        }

    @_retry
    def _db_next_id(self) -> int:
        items = self.get_all_items()
        return max((it["id"] for it in items), default=0) + 1

    @_retry
    def upsert_item(self, name: str, category: str,
                    price_buy: Optional[float] = None,
                    price_sell: Optional[float] = None,
                    emoji: str = "") -> dict:
        """Insert or update an item in the DataBase."""
        from datetime import datetime, timezone, timedelta
        now = (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%d.%m.%Y %H:%M")
        existing = self.find_item(name)
        if existing:
            row_num = None
            cell = self._sheet.find(name, in_column=COL_DB_NAME)
            if cell:
                row_num = cell.row
            updates = {}
            if category:
                updates[COL_DB_CATEGORY] = category
            if price_buy is not None:
                updates[COL_DB_PRICE_BUY] = price_buy
            if price_sell is not None:
                updates[COL_DB_PRICE_SELL] = price_sell
            if emoji:
                updates[COL_DB_EMOJI] = emoji
            updates[COL_DB_UPDATED] = now
            for col, val in updates.items():
                self._sheet.update_cell(row_num, col, val)
            existing.update(updates)
            existing["updated_at"] = now
            return existing
        new_id = self._db_next_id()
        row = [new_id, name, category,
               price_buy if price_buy is not None else "",
               price_sell if price_sell is not None else "",
               emoji, now]
        # Write to AA-AG columns instead of A-G
        items = self.get_all_items()
        next_row = DATA_START_ROW + len(items)
        cell_range = f"AA{next_row}:AG{next_row}"
        self._sheet.update(cell_range, [row], value_input_option="USER_ENTERED")
        return {
            "id": new_id, "name": name, "category": category,
            "price_buy": price_buy, "price_sell": price_sell,
            "emoji": emoji, "updated_at": now,
        }

    @_retry
    def delete_item(self, name: str) -> bool:
        """Delete item by name from the DataBase."""
        cell = self._sheet.find(name, in_column=COL_DB_NAME)
        if cell is None:
            return False
        self._sheet.delete_rows(cell.row)
        return True

    @_retry
    def get_transactions(self, start_row: int = DATA_START_ROW, end_row: int = 2000) -> list[list]:
        """Get all transaction rows (A:H columns)."""
        return self._sheet.get(f"A{start_row}:H{end_row}")
