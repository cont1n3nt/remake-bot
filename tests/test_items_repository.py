"""Тесты работы с базой предметов (DataBase AA:AG) — пункты 17, 18, 22.

Лист Google Sheets подменён минимальной заглушкой: нас интересует, В КАКУЮ
строку репозиторий пишет и какие строки он вообще видит, а не сетевой слой.
"""
import pytest

from bot.config.constants import (
    DATA_START_ROW, COL_DB_PRICE_BUY, COL_DB_PRICE_SELL, COL_DB_UPDATED,
)
from bot.repositories.sheets_repository import SheetsRepository
from bot.utils.embeds import normalize_emoji_name


class FakeSheet:
    """AA:AG-строки как список списков; DATA_START_ROW соответствует rows[0]."""

    def __init__(self, rows):
        self.rows = rows
        self.row_count = DATA_START_ROW + len(rows) + 10
        self.updated_cells: list[tuple[int, int, object]] = []
        self.deleted_rows: list[int] = []
        self.appended: list[tuple[str, list]] = []

    def get(self, _range):
        return self.rows

    def update_cell(self, row, col, value):
        self.updated_cells.append((row, col, value))

    def update(self, cell_range, values, **_kwargs):
        self.appended.append((cell_range, values))

    def delete_rows(self, row):
        self.deleted_rows.append(row)


def make_repo(rows) -> tuple[SheetsRepository, FakeSheet]:
    repo = SheetsRepository.__new__(SheetsRepository)  # без сетевой инициализации
    sheet = FakeSheet(rows)
    repo._sheet = sheet
    return repo, sheet


# Один и тот же «Топот» в двух категориях — ровно случай из пункта 17.
DUPLICATE_NAME_ROWS = [
    ["1", "Топот", "resource", "4000", "", "topot", "30.07.2026 03:44"],   # строка 3
    ["2", "Мякоть", "resource", "2100", "", "myakot", "30.07.2026 03:49"],  # строка 4
    ["3", "Топот", "boost", "", "6700", "topot", "30.07.2026 03:44"],       # строка 5
]


def test_get_all_items_reports_real_row_numbers():
    repo, _ = make_repo(DUPLICATE_NAME_ROWS)
    items = repo.get_all_items()
    assert [it["row"] for it in items] == [DATA_START_ROW, DATA_START_ROW + 1, DATA_START_ROW + 2]


def test_setboost_updates_boost_row_not_first_name_match():
    """Пункт 17: /setboost «Топот» обязан менять строку буста (5), а не первую
    попавшуюся строку с этим именем (3, ресурс)."""
    repo, sheet = make_repo(DUPLICATE_NAME_ROWS)

    repo.upsert_item("Топот", "boost", price_sell=6000)

    written_rows = {row for row, _col, _val in sheet.updated_cells}
    assert written_rows == {DATA_START_ROW + 2}, "запись ушла не в строку буста"
    price_writes = [(r, c, v) for r, c, v in sheet.updated_cells if c == COL_DB_PRICE_SELL]
    assert price_writes == [(DATA_START_ROW + 2, COL_DB_PRICE_SELL, 6000)]


def test_setprice_updates_resource_row_of_same_name():
    """Зеркальная проверка: /setprice того же имени идёт в строку ресурса."""
    repo, sheet = make_repo(DUPLICATE_NAME_ROWS)

    repo.upsert_item("Топот", "resource", price_buy=6000)

    price_writes = [(r, c, v) for r, c, v in sheet.updated_cells if c == COL_DB_PRICE_BUY]
    assert price_writes == [(DATA_START_ROW, COL_DB_PRICE_BUY, 6000)]


def test_upsert_never_touches_category_column():
    repo, sheet = make_repo(DUPLICATE_NAME_ROWS)
    repo.upsert_item("Топот", "boost", price_sell=6000)
    touched_cols = {col for _row, col, _val in sheet.updated_cells}
    assert touched_cols <= {COL_DB_PRICE_SELL, COL_DB_UPDATED}


# --- Пункт 18: строка с «плохим» ID больше не теряется -------------------

def test_row_with_non_numeric_id_is_not_dropped():
    """Раньше int(row[0]) кидал ValueError и строка молча выпадала из выдачи —
    /setboost отвечал «не найден» на существующий буст."""
    rows = [
        ["1", "Мякоть", "resource", "2100", "", "", ""],
        ["", "Катализатор", "boost", "", "110000", "kata", ""],       # пустой ID
        ["n/a", "Схрон мастера", "boost", "", "100000", "shron", ""],  # мусорный ID
    ]
    repo, _ = make_repo(rows)
    names = [it["name"] for it in repo.get_all_items()]
    assert names == ["Мякоть", "Катализатор", "Схрон мастера"]


def test_boost_with_broken_id_is_findable_by_category():
    rows = [["n/a", "Катализатор", "boost", "", "110000", "kata", ""]]
    repo, _ = make_repo(rows)
    found = repo.find_item_by_name_and_category("катализатор", "BOOST")
    assert found is not None
    assert found["row"] == DATA_START_ROW


def test_new_item_appends_after_last_occupied_row():
    repo, sheet = make_repo(DUPLICATE_NAME_ROWS)
    repo.upsert_item("Новый предмет", "resource", price_buy=100)
    cell_range, values = sheet.appended[0]
    expected_row = DATA_START_ROW + len(DUPLICATE_NAME_ROWS)
    assert cell_range == f"AA{expected_row}:AG{expected_row}"
    assert values[0][1] == "Новый предмет"
    assert values[0][0] == 4  # max(id) + 1


# --- delete_item ---------------------------------------------------------

def test_delete_refuses_ambiguous_name_without_category():
    repo, sheet = make_repo(DUPLICATE_NAME_ROWS)
    assert repo.delete_item("Топот") is False
    assert sheet.deleted_rows == []


def test_delete_with_category_removes_correct_row():
    repo, sheet = make_repo(DUPLICATE_NAME_ROWS)
    assert repo.delete_item("Топот", "boost") is True
    assert sheet.deleted_rows == [DATA_START_ROW + 2]


# --- Пункт 22: в колонку эмодзи пишется только имя -----------------------

@pytest.mark.parametrize("raw,expected", [
    ("<:topot:123456789>", "topot"),
    ("<a:topot:123456789>", "topot"),
    (":topot:", "topot"),
    ("topot", "topot"),
    ("  <:my_emoji:1>  ", "my_emoji"),
    ("", ""),
    ("🚀", "🚀"),
])
def test_normalize_emoji_name(raw, expected):
    assert normalize_emoji_name(raw) == expected
