"""Тесты записи сделок в лист (пункт 20): точка вставки и идемпотентный повтор."""
import pytest
from gspread.exceptions import APIError

from bot.config.constants import DATA_START_ROW
from bot.repositories.sheets_repository import SheetsRepository


def _api_error(message: str = "backend error") -> APIError:
    class FakeResponse:
        status_code = 500
        text = message

        @staticmethod
        def json():
            return {"error": {"code": 500, "message": message, "status": "INTERNAL"}}

    return APIError(FakeResponse())


class FakeSheet:
    """Колонка B (ник) + строки A:E для перечитывания при повторе."""

    def __init__(self, column_b, rows_ae=None):
        self.column_b = column_b
        self.rows_ae = rows_ae or []
        self.inserted: list[tuple[list, int]] = []
        self.fail_times = 0
        self.land_before_failing = False

    def col_values(self, _col):
        return self.column_b

    def get(self, _range):
        return self.rows_ae

    def insert_row(self, row, index):
        if self.fail_times > 0:
            self.fail_times -= 1
            if self.land_before_failing:
                # Запись прошла, но ответ не дошёл — классический источник дублей.
                self.inserted.append((row, index))
                self.rows_ae.append(row[:5])
            raise _api_error()
        self.inserted.append((row, index))
        self.rows_ae.append(row[:5])


def make_repo(sheet) -> SheetsRepository:
    repo = SheetsRepository.__new__(SheetsRepository)
    repo._sheet = sheet
    return repo


# --- _last_row: конец непрерывного блока, а не глобальный максимум --------

def test_last_row_is_end_of_contiguous_block():
    # B1,B2 — шапка; строки 3..5 заняты; дальше разрыв и мусор далеко внизу.
    column_b = ["Ник", "", "vasya", "petya", "kolya", "", "", "", "мусор"]
    repo = make_repo(FakeSheet(column_b))
    assert repo._last_row() == 5


def test_last_row_ignores_stray_value_far_below():
    """Именно это уводило /add «под таблицу»: одно значение на строке 900."""
    column_b = ["Ник", ""] + ["nick"] * 3 + [""] * 894 + ["случайный текст"]
    repo = make_repo(FakeSheet(column_b))
    assert repo._last_row() == DATA_START_ROW + 2


def test_last_row_on_empty_block():
    repo = make_repo(FakeSheet(["Ник", ""]))
    assert repo._last_row() == DATA_START_ROW - 1


def test_append_transaction_inserts_right_after_block():
    column_b = ["Ник", "", "vasya", "petya"]
    sheet = FakeSheet(column_b)
    repo = make_repo(sheet)
    repo._copy_formulas_to_new_row = lambda index: None
    repo._ensure_jrow = lambda nickname: None

    repo.append_transaction("www", "buy", 1000000)

    _row, index = sheet.inserted[0]
    assert index == 5  # сразу за последней занятой строкой блока (4)


# --- идемпотентный повтор -------------------------------------------------

def test_retry_does_not_duplicate_when_row_already_landed():
    """APIError после успешной записи не должна порождать вторую строку."""
    sheet = FakeSheet(["Ник", "", "vasya"])
    sheet.fail_times = 1
    sheet.land_before_failing = True
    repo = make_repo(sheet)
    repo._copy_formulas_to_new_row = lambda index: None
    repo._ensure_jrow = lambda nickname: None

    repo.append_transaction("www", "buy", 1000000)

    assert len(sheet.inserted) == 1, "сделка записана дважды"


def test_retry_still_retries_when_row_did_not_land():
    """Если запись действительно не прошла — повтор обязан состояться."""
    sheet = FakeSheet(["Ник", "", "vasya"])
    sheet.fail_times = 1
    sheet.land_before_failing = False
    repo = make_repo(sheet)
    repo._copy_formulas_to_new_row = lambda index: None
    repo._ensure_jrow = lambda nickname: None

    repo.append_transaction("www", "buy", 1000000)

    assert len(sheet.inserted) == 1


def test_zero_amount_is_never_written():
    sheet = FakeSheet(["Ник", "", "vasya"])
    repo = make_repo(sheet)
    repo.append_transaction("www", "buy", 0)
    assert sheet.inserted == []
