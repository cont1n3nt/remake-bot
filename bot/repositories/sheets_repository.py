import logging
import time
import functools
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials
from gspread.exceptions import APIError

from bot.config.constants import (
    COL_NICKNAME, COL_BUY, COL_SELL, COL_AMOUNT,
    COL_REFERRED_BY, COL_DISCORD_ID, COL_UNIQUE_NICK,
    COL_TOTAL_COINS, COL_TOTAL_XP, COL_RANK,
    COL_REFERRAL_COUNT, COL_REFERRAL_ROLE, COL_BOOSTER,
    COL_TOTAL_TURNOVER,
    COL_DB_ID, COL_DB_NAME, COL_DB_CATEGORY,
    COL_DB_PRICE_BUY, COL_DB_PRICE_SELL, COL_DB_EMOJI, COL_DB_UPDATED,
    DATA_START_ROW, MAX_RETRIES, RETRY_MIN_WAIT,
    SYNC_SHEET_SKUP, SYNC_SHEET_SKUP_BOOST, SYNC_SHEET_BOOST_SALE,
    SYNC_COLUMN_PAIRS_FULL, SYNC_COLUMN_PAIRS_HALF,
    SYNC_MAX_ROWS_SKUP, SYNC_MAX_ROWS_BOOST, SYNC_MAX_ROWS_SKUP_BOOST,
)

logger = logging.getLogger("bot")


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

    def _find_cell_icase(self, value: str, column: int) -> Optional[dict]:
        """Find cell by value case-insensitively in a given column.
        Returns gspread Cell object or None."""
        try:
            vals = self._sheet.col_values(column)
            for i, v in enumerate(vals):
                if v.strip().lower() == value.lower():
                    class FakeCell:
                        row = i + 1
                    return FakeCell()
        except Exception as e:
            logger.warning("_find_cell_icase failed for column %s, value %r: %s", column, value, e)
        return None

    def _find_all_icase(self, value: str, column: int) -> list:
        """Find all cells matching value case-insensitively in a column."""
        result = []
        try:
            vals = self._sheet.col_values(column)
            for i, v in enumerate(vals):
                if v.strip().lower() == value.lower():
                    class FakeCell:
                        row = i + 1
                    result.append(FakeCell())
        except Exception as e:
            logger.warning("_find_all_icase failed for column %s, value %r: %s", column, value, e)
        return result

    @_retry
    def find_user(self, nickname: str) -> Optional[dict]:
        """Return user stats by nickname from the user database (J-S columns).
        Finds the user in column J (Уникальный ник) and reads data
        from J-S columns of that same row. Case-insensitive."""
        nickname_lower = nickname.lower()
        cell = self._find_cell_icase(nickname_lower, COL_UNIQUE_NICK)
        if cell is None:
            return None

        # Read all data from the J-row (user database section)
        vals = self._sheet.row_values(cell.row)

        # Read referred_by from ticket rows (column H)
        referred_by = None
        ticket_cells = self._find_all_icase(nickname_lower, COL_NICKNAME)
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
        """Последняя строка НЕПРЕРЫВНОГО блока тикетов в колонке B.

        Раньше здесь брался глобальный максимум по всей колонке — любое
        случайное значение далеко внизу листа уводило точку вставки за пределы
        таблицы, и новые сделки записывались «под таблицу», мимо формул
        (пункт 20). Теперь блок заканчивается на первом разрыве после
        DATA_START_ROW.
        """
        vals = self._sheet.col_values(COL_NICKNAME)
        last = DATA_START_ROW - 1
        for row in range(DATA_START_ROW, len(vals) + 1):
            value = vals[row - 1]
            if value is None or not str(value).strip():
                break
            last = row
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
        """Copy K-U formulas to the UNIQUE row for this nickname if empty.
        Case-insensitive."""
        try:
            cell = self._find_cell_icase(nickname, COL_UNIQUE_NICK)
            if cell is None:
                return
            k_raw = self._sheet.cell(cell.row, COL_TOTAL_COINS).value
            if k_raw and str(k_raw).strip():
                return
            src_row = None
            for b_cell in self._find_all_icase(nickname, COL_NICKNAME):
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
        except Exception as e:
            logger.warning("_ensure_jrow failed for %s: %s", nickname, e)

    @_retry
    def ensure_user(self, nickname: str) -> bool:
        """Check if nickname exists in column B (ticket section).
        Does NOT create intermediate rows — only checks existence.
        Case-insensitive."""
        cell = self._find_cell_icase(nickname, COL_NICKNAME)
        return cell is not None

    def append_transaction(
        self, nickname: str, tx_type: str, amount: float,
        referrer: str | None = None,
    ) -> None:
        """Append a transaction row atomically — only when all data is known.

        Validates that amount > 0 before writing.
        Nickname is stored lowercase for case-insensitive matching.
        """
        if amount <= 0:
            return
        from datetime import datetime, timezone, timedelta
        now = (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%d.%m.%y %H:%M")
        nickname = nickname.lower().strip()
        index = self._last_row() + 1
        row = [now, nickname, True, False, amount, "", "", referrer or ""]

        if tx_type == "buy":
            self._insert_row_retry(row, index)
        else:
            row[2], row[3] = False, True
            self._insert_row_retry(row, index)

        self._copy_formulas_to_new_row(index)
        self._ensure_jrow(nickname)
        if referrer:
            self._ensure_jrow(referrer)

    def _row_already_present(self, row: list, index: int) -> bool:
        """Проверить, легла ли строка на лист, — по (дата, ник, сумма).

        Нужна для идемпотентного повтора: Google Sheets может вернуть 5xx уже
        ПОСЛЕ успешной записи, и слепой retry дублировал сделку (пункт 20)."""
        date_val, nick_val, amount_val = str(row[0]), str(row[1]), row[4]
        try:
            # Небольшое окно вокруг ожидаемой позиции — строка могла сдвинуться
            # на пару позиций, если параллельно вставили ещё одну.
            start = max(DATA_START_ROW, index - 3)
            existing = self._sheet.get(f"A{start}:E{index + 3}")
        except APIError as e:
            logger.warning("_row_already_present: не удалось перечитать лист: %s", e)
            return False
        for existing_row in existing:
            if len(existing_row) < 5:
                continue
            same_date = str(existing_row[0]).strip() == date_val.strip()
            same_nick = str(existing_row[1]).strip().lower() == nick_val.strip().lower()
            same_amount = self._parse_float(existing_row[4]) == self._parse_float(amount_val)
            if same_date and same_nick and same_amount:
                return True
        return False

    def _insert_row_retry(self, row: list, index: int) -> None:
        """Insert a single row, retrying only when the row demonstrably did not
        land. Separate method so the retry covers just the insert, not the
        entire transaction flow."""
        last_exc = None
        for attempt in range(MAX_RETRIES):
            try:
                self._sheet.insert_row(row, index)
                return
            except APIError as e:
                last_exc = e
                if self._row_already_present(row, index):
                    logger.warning(
                        "insert_row вернул ошибку, но строка уже на листе — повтор отменён: %s", e,
                    )
                    return
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_MIN_WAIT * (2 ** attempt))
        raise last_exc

    @_retry
    def set_referred_by(self, nickname: str, referrer: str) -> None:
        """Update column H (Пришел от) for every transaction row of this user.
        Case-insensitive."""
        cells = self._find_all_icase(nickname, COL_NICKNAME)
        for cell in cells:
            self._sheet.update_cell(cell.row, COL_REFERRED_BY, referrer)

    @_retry
    def set_discord_id(self, unique_nick: str, discord_id: str) -> bool:
        """Записать Discord ID в колонку I напротив уникального ника (колонка J).
        Возвращает False, если такого ника нет в базе пользователей."""
        cell = self._find_cell_icase(unique_nick.strip(), COL_UNIQUE_NICK)
        if cell is None:
            logger.warning("set_discord_id: ник %r не найден в колонке J", unique_nick)
            return False
        # Строго RAW: при USER_ENTERED (умолчание update_cell) 18-значный
        # Discord ID превращается в число и теряет точность — 7.05383E+17.
        a1 = gspread.utils.rowcol_to_a1(cell.row, COL_DISCORD_ID)
        self._sheet.update(a1, [[str(discord_id)]], value_input_option="RAW")
        return True

    @_retry
    def find_referrals(self, nickname: str) -> list[dict]:
        """Find all users who have this nickname in column H (Пришел от).
        Case-insensitive."""
        cells = self._find_all_icase(nickname, COL_REFERRED_BY)

        result = []
        for cell in cells:
            nick_raw = self._sheet.cell(cell.row, COL_NICKNAME).value
            result.append({
                "row": cell.row,
                "nickname": nick_raw.strip() if nick_raw else "",
            })
        return result

    @_retry
    def get_user_nicknames(self) -> list[str]:
        """Return all unique nicknames from column J (user database)."""
        return self._sheet.col_values(COL_UNIQUE_NICK)[DATA_START_ROW - 1:]

    @_retry
    def user_has_referral(self, nickname: str) -> bool:
        """Check if user already has a referrer set in column H.
        Case-insensitive."""
        cells = self._find_all_icase(nickname, COL_NICKNAME)
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
        """Return all items from the DataBase section (AA:AG columns).

        Каждый элемент несёт свой реальный номер строки листа в ключе "row" —
        только по нему можно безопасно писать обратно: искать строку повторно
        по одному лишь имени нельзя, у предметов из разных категорий имена
        совпадают (см. upsert_item / delete_item).

        ID разбирается через _parse_int, а не int(): раньше нечисловой ID
        выбрасывал ValueError, и строка молча пропадала из выдачи целиком —
        предмет переставал находиться в /setboost и /sync_prices.
        """
        vals = self._sheet.get(f"AA{DATA_START_ROW}:AG{self._sheet.row_count}")
        items = []
        for offset, row in enumerate(vals):
            if len(row) < 2 or not str(row[1]).strip():
                continue
            items.append({
                "row": DATA_START_ROW + offset,
                "id": self._parse_int(row[0]) if row[0] else 0,
                "name": str(row[1]).strip(),
                "category": str(row[2]).strip() if len(row) > 2 else "",
                "price_buy": self._parse_float(row[3]) if len(row) > 3 else None,
                "price_sell": self._parse_float(row[4]) if len(row) > 4 else None,
                "emoji": str(row[5]).strip() if len(row) > 5 else "",
                "updated_at": str(row[6]).strip() if len(row) > 6 else "",
            })
        return items

    @_retry
    def find_item(self, name: str) -> Optional[dict]:
        """Find item by name in the DataBase (column AB). Case-insensitive."""
        cell = self._find_cell_icase(name, COL_DB_NAME)
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

    @staticmethod
    def _match_item(items: list[dict], name: str, category: str) -> Optional[dict]:
        """Найти предмет по имени И категории в уже прочитанном списке."""
        name_key = name.strip().lower()
        cat_key = category.strip().lower()
        for it in items:
            if it["name"].strip().lower() == name_key and it["category"].strip().lower() == cat_key:
                return it
        return None

    @_retry
    def find_item_by_name_and_category(self, name: str, category: str) -> Optional[dict]:
        """Find item by name AND category. Case-insensitive for both.
        Возвращает элемент вместе с номером строки ("row") — см. get_all_items."""
        return self._match_item(self.get_all_items(), name, category)

    @_retry
    def _db_next_id(self) -> int:
        items = self.get_all_items()
        return max((it["id"] for it in items), default=0) + 1

    @_retry
    def upsert_item(self, name: str, category: str,
                    price_buy: Optional[float] = None,
                    price_sell: Optional[float] = None,
                    emoji: str = "") -> dict:
        """Insert or update an item in the DataBase.
        Category is NEVER overwritten on existing items — it's read-only.
        Search is done by (name + category) to avoid conflicts between
        same-named items in different categories (e.g. boost vs resource)."""
        from datetime import datetime, timezone, timedelta
        now = (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%d.%m.%Y %H:%M")
        items = self.get_all_items()
        existing = self._match_item(items, name, category)
        if existing:
            # Пишем строго по строке, найденной вместе с категорией. Раньше
            # здесь был повторный поиск _find_cell_icase(name, COL_DB_NAME) —
            # он возвращал ПЕРВУЮ строку с таким именем без учёта категории,
            # из-за чего /setboost менял цену одноимённого ресурса.
            row_num = existing["row"]
            updates = {}
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
        new_id = max((it["id"] for it in items), default=0) + 1
        row = [new_id, name, category,
               price_buy if price_buy is not None else "",
               price_sell if price_sell is not None else "",
               emoji, now]
        # Write to AA-AG columns instead of A-G. Точку вставки берём от реальной
        # последней занятой строки, а не от количества предметов: при дырке в
        # середине списка счётчик затирал бы существующую строку.
        next_row = max((it["row"] for it in items), default=DATA_START_ROW - 1) + 1
        cell_range = f"AA{next_row}:AG{next_row}"
        self._sheet.update(cell_range, [row], value_input_option="USER_ENTERED")
        return {
            "id": new_id, "name": name, "category": category,
            "price_buy": price_buy, "price_sell": price_sell,
            "emoji": emoji, "updated_at": now,
        }

    @_retry
    def delete_item(self, name: str, category: Optional[str] = None) -> bool:
        """Delete item by name from the DataBase. Case-insensitive.

        Если категория задана — удаляется строго строка этой категории; без неё
        удаляется единственное совпадение по имени, а при неоднозначности
        (одно имя в двух категориях) не удаляется ничего, чтобы не снести чужую
        строку молча."""
        items = self.get_all_items()
        name_key = name.strip().lower()
        matches = [it for it in items if it["name"].strip().lower() == name_key]
        if category is not None:
            matches = [it for it in matches if it["category"].strip().lower() == category.strip().lower()]
        if len(matches) != 1:
            if len(matches) > 1:
                logger.warning(
                    "delete_item: %r найден в нескольких категориях (%s) — удаление отменено",
                    name, ", ".join(it["category"] for it in matches),
                )
            return False
        self._sheet.delete_rows(matches[0]["row"])
        return True

    @_retry
    def get_transactions(self, start_row: int = DATA_START_ROW, end_row: int = 2000) -> list[list]:
        """Get all transaction rows (A:H columns)."""
        return self._sheet.get(f"A{start_row}:H{end_row}")

    def _get_worksheet(self, name: str):
        """Get a worksheet by name from the spreadsheet."""
        try:
            return self._spreadsheet.worksheet(name)
        except Exception:
            return None

    @_retry
    def _find_item_cell_in_sheet(self, ws, item_name: str) -> tuple:
        """Find item name in ANY column of a worksheet.
        Returns (row_index, col_index) 1-based, or (None, None) if not found.
        Writes price to cell LEFT of the found name cell."""
        try:
            all_vals = ws.get_all_values()
            for row_idx, row in enumerate(all_vals):
                for col_idx, val in enumerate(row):
                    if val.strip().lower() == item_name.lower():
                        return (row_idx + 1, col_idx + 1)
        except Exception as e:
            logger.warning("_find_item_cell_in_sheet failed for %r on sheet %s: %s", item_name, ws, e)
        return (None, None)

    def service_account_email(self) -> str:
        """E-mail сервис-аккаунта — его нужно назвать пользователю, когда
        Google Sheets отклоняет запись из-за защищённого диапазона."""
        return getattr(self._client.auth, "service_account_email", "") or ""

    def protected_sheet_titles(self) -> set[str]:
        """Названия листов, на которых есть защищённые диапазоны.

        Обойти защиту кодом нельзя — права выдаются владельцем таблицы. Зато
        можно предупредить об этом ДО попытки записи и назвать точную причину
        (см. пункт 23: APIError 400 "You are trying to edit a protected cell")."""
        titles: set[str] = set()
        try:
            sheet_id_to_title = {ws.id: ws.title for ws in self._spreadsheet.worksheets()}
            for pr in self._spreadsheet.list_protected_ranges():
                sheet_id = pr.get("range", {}).get("sheetId")
                title = sheet_id_to_title.get(sheet_id)
                if title:
                    titles.add(title)
        except Exception as e:
            logger.warning("protected_sheet_titles failed: %s", e)
        return titles

    def _collect_sheet_names(self, ws, column_pairs, max_rows) -> set:
        """Собрать все названия из листа для отчёта о ненайденных."""
        names = set()
        if ws is None:
            return names
        try:
            for name_col, _ in column_pairs:
                vals = ws.col_values(name_col)
                for row_idx, val in enumerate(vals):
                    if row_idx >= max_rows:
                        break
                    n = val.strip().lower()
                    if n:
                        names.add(n)
        except Exception as e:
            logger.warning("_collect_sheet_names failed for sheet %s: %s", ws, e)
        return names

    @_retry
    def sync_prices_to_sheets(self) -> dict:
        """Sync prices from DataBase to 3 external sheets using fixed column pairs.

        Rules per sheet:
          1. Мейн скуп → resource items, max 31 rows
             name cols: C,J,Q,X,AE,AL,AS → price cols: D,K,R,Y,AF,AM,AT
          2. Скуп бустов → resource items, max 9 rows
             name cols: C,J,Q,X → price cols: D,K,R,Y
          3. БУСТЫ → boost items, max 9 rows
             name cols: C,J,Q,X,AE,AL,AS → price cols: D,K,R,Y,AF,AM,AT

        Returns dict with counts: resource, skup_boost, boost, not_found.
        """
        items = self.get_all_items()

        resource_by_name: dict[str, dict] = {}
        boost_by_name: dict[str, dict] = {}
        for it in items:
            name_lower = it["name"].strip().lower()
            if it["category"] == "resource" and it.get("price_buy") is not None:
                resource_by_name[name_lower] = it
            elif it["category"] == "boost" and it.get("price_sell") is not None:
                boost_by_name[name_lower] = it

        errors: list[str] = []
        protected_titles = self.protected_sheet_titles()
        protected_hit: list[str] = []

        def _sync_sheet(ws, column_pairs, max_rows, lookup, price_key):
            if ws is None:
                return 0
            cells = []
            for name_col, price_col in column_pairs:
                try:
                    vals = ws.col_values(name_col)
                except APIError as e:
                    errors.append(f"{ws.title}: не удалось прочитать колонку {name_col}: {e}")
                    continue
                for row_idx, val in enumerate(vals):
                    if row_idx >= max_rows:
                        break
                    name = val.strip().lower()
                    if name in lookup:
                        price = lookup[name].get(price_key)
                        if price is not None:
                            cells.append(gspread.Cell(row_idx + 1, price_col, price))
            if not cells:
                return 0
            # Один батч-запрос на весь лист вместо одной ячейки за раз —
            # иначе при большом числе совпадений упираемся в квоту Google Sheets API
            # (ошибки раньше молча проглатывались, из-за чего часть цен не обновлялась).
            try:
                ws.update_cells(cells, value_input_option="USER_ENTERED")
            except APIError as e:
                # Защиту диапазона кодом не обойти — сообщаем причину прямо,
                # вместо голого текста APIError (пункт 23).
                if "protected" in str(e).lower() or ws.title in protected_titles:
                    protected_hit.append(ws.title)
                    errors.append(
                        f"{ws.title}: лист защищён, запись {len(cells)} ячеек отклонена"
                    )
                else:
                    errors.append(f"{ws.title}: ошибка записи {len(cells)} ячеек: {e}")
                return 0
            return len(cells)

        result = {
            "resource": 0, "skup_boost": 0, "boost": 0,
            "not_found": [], "errors": errors,
            "protected": protected_hit,
            "service_account": self.service_account_email(),
        }

        # Собираем все названия из листов для отчёта
        all_sheet_names = set()

        # 1) Мейн скуп → resource items, max 31 rows, full column pairs
        ws_skup = self._get_worksheet(SYNC_SHEET_SKUP)
        if ws_skup is None:
            errors.append(f"Лист «{SYNC_SHEET_SKUP}» не найден")
        result["resource"] = _sync_sheet(ws_skup, SYNC_COLUMN_PAIRS_FULL, SYNC_MAX_ROWS_SKUP, resource_by_name, "price_buy")
        all_sheet_names |= self._collect_sheet_names(ws_skup, SYNC_COLUMN_PAIRS_FULL, SYNC_MAX_ROWS_SKUP)

        # 2) Скуп бустов → resource items, max 9 rows, half column pairs
        ws_skup_boost = self._get_worksheet(SYNC_SHEET_SKUP_BOOST)
        if ws_skup_boost is None:
            errors.append(f"Лист «{SYNC_SHEET_SKUP_BOOST}» не найден")
        result["skup_boost"] = _sync_sheet(ws_skup_boost, SYNC_COLUMN_PAIRS_HALF, SYNC_MAX_ROWS_SKUP_BOOST, resource_by_name, "price_buy")
        all_sheet_names |= self._collect_sheet_names(ws_skup_boost, SYNC_COLUMN_PAIRS_HALF, SYNC_MAX_ROWS_SKUP_BOOST)

        # 3) БУСТЫ → boost items, max 9 rows, full column pairs
        ws_boost_sale = self._get_worksheet(SYNC_SHEET_BOOST_SALE)
        if ws_boost_sale is None:
            errors.append(f"Лист «{SYNC_SHEET_BOOST_SALE}» не найден")
        result["boost"] = _sync_sheet(ws_boost_sale, SYNC_COLUMN_PAIRS_FULL, SYNC_MAX_ROWS_BOOST, boost_by_name, "price_sell")
        all_sheet_names |= self._collect_sheet_names(ws_boost_sale, SYNC_COLUMN_PAIRS_FULL, SYNC_MAX_ROWS_BOOST)

        # Находим предметы, которые есть в базе, но не найдены в листах
        db_names = set(resource_by_name.keys()) | set(boost_by_name.keys())
        not_found = db_names - all_sheet_names
        result["not_found"] = sorted(not_found)

        return result
