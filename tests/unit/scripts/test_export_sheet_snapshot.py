"""Тесты `scripts/export_sheet_snapshot.py` на синтетических данных.

Живой Google Sheets здесь не нужен: `FakeRangeReader` реализует протокол
`RangeReader` по словарю `{(sheet, a1, render_option): rows}`, поэтому
проверяется только логика сборки/склейки/CSV-записи, а не сеть. Полноценный
смоук-тест на реальные объёмы (657/244/0/225) — по `sqlite_migration.md`
§VI.1 — прогоняется владельцем против живой таблицы при первом запуске.
"""

import csv
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

_MODULE_PATH = Path(__file__).resolve().parents[3] / "scripts" / "export_sheet_snapshot.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("export_sheet_snapshot", _MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mod = _load_module()


class FakeRangeReader:
    """`RangeReader` фейк: возвращает заранее заданную сетку по ключу."""

    def __init__(self, table: dict[tuple[str, str, str], list[list[object]]]) -> None:
        self._table = table
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, sheet: str, a1: str, value_render_option: str) -> list[list[object]]:
        key = (sheet, a1, value_render_option)
        self.calls.append(key)
        return self._table.get(key, [])


def test_find_last_data_row_trims_trailing_empty() -> None:
    last = 3 + mod._MAX_SCAN_ROWS - 1
    key = ("DataBase", mod.a1_range("DataBase", "B", 3, "B", last), "FORMATTED_VALUE")
    reader = FakeRangeReader(
        {
            key: [
                ["nick1"],
                ["nick2"],
                [""],
                ["nick4"],
            ]
        }
    )
    assert mod.find_last_data_row(reader, "DataBase", "B", 3) == 6


def test_find_last_data_row_empty_block() -> None:
    reader = FakeRangeReader({})
    assert mod.find_last_data_row(reader, "DataBase", "U", 3) == 2


def test_read_block_rows_mixed_render_options_preserve_blank_rows(tmp_path: Path) -> None:
    """Колонка A читается FORMATTED_VALUE, остальные UNFORMATTED_VALUE; пустая
    строка внутри диапазона не пропускается (§I.3, §VI.1)."""
    block = mod.SKUPKA_BLOCKS[0]  # Тикеты, A:H, anchor B, formatted={"A"}
    reader = FakeRangeReader(
        {
            ("DataBase", mod.a1_range("DataBase", "A", 3, "A", 5), "FORMATTED_VALUE"): [
                ["27.07.26 21:31"],
                [""],
                ["28.07.26 10:00"],
            ],
            ("DataBase", mod.a1_range("DataBase", "B", 3, "H", 5), "UNFORMATTED_VALUE"): [
                ["nick1", 100, "", 100, 1, 5, ""],
                [],
                ["nick3", "", 200, 200, 2, 10, "referrer"],
            ],
        }
    )
    rows = mod._read_block_rows(reader, block, 3, 5)
    assert len(rows) == 3
    assert rows[0] == [3, "27.07.26 21:31", "nick1", 100, "", 100, 1, 5, ""]
    assert rows[1] == [4, "", "", "", "", "", "", "", ""]
    assert rows[2] == [5, "28.07.26 10:00", "nick3", "", 200, 200, 2, 10, "referrer"]


def test_export_skupka_database_writes_csv_with_sheet_row(tmp_path: Path) -> None:
    last = 3 + mod._MAX_SCAN_ROWS - 1
    reader = FakeRangeReader(
        {
            ("DataBase", mod.a1_range("DataBase", "B", 3, "B", last), "FORMATTED_VALUE"): [
                ["nick1"],
                ["nick2"],
            ],
            ("DataBase", mod.a1_range("DataBase", "A", 3, "A", 4), "FORMATTED_VALUE"): [
                ["27.07.26 21:31"],
                ["28.07.26 10:00"],
            ],
            ("DataBase", mod.a1_range("DataBase", "B", 3, "H", 4), "UNFORMATTED_VALUE"): [
                ["nick1", 100, 0, 100, 1, 5, ""],
                ["nick2", 0, 200, 200, 2, 10, ""],
            ],
            ("DataBase", mod.a1_range("DataBase", "J", 3, "J", last), "FORMATTED_VALUE"): [],
            ("DataBase", mod.a1_range("DataBase", "U", 3, "U", last), "FORMATTED_VALUE"): [],
            ("DataBase", mod.a1_range("DataBase", "AA", 3, "AA", last), "FORMATTED_VALUE"): [],
        }
    )
    volumes = mod.export_skupka_database(reader, tmp_path)
    assert volumes["Тикеты"] == 2
    assert volumes["Общая база пользователей"] == 0
    assert volumes["Магазин"] == 0
    assert volumes["item database"] == 0

    with (tmp_path / "tickets.csv").open(encoding="utf-8") as f:
        rows = list(csv.reader(f))
    assert rows[0] == [
        "sheet_row",
        "Дата",
        "Ник",
        "Покупка",
        "Продажа",
        "Сумма",
        "Coins",
        "XP",
        "Пришел от:",
    ]
    assert rows[1] == ["3", "27.07.26 21:31", "nick1", "100", "0", "100", "1", "5", ""]
    assert rows[2] == ["4", "28.07.26 10:00", "nick2", "0", "200", "200", "2", "10", ""]


def test_export_formulas_writes_frozen_json(tmp_path: Path) -> None:
    reader = FakeRangeReader(
        {
            ("DataBase", mod.a1_range("DataBase", cell[0], int(cell[1:])), "FORMULA"): [
                [f"={cell}-formula"]
            ]
            for cell in mod.FORMULA_CELLS
        }
    )
    mod.export_formulas(reader, tmp_path)
    data = json.loads((tmp_path / "formulas.json").read_text(encoding="utf-8"))
    assert set(data) == set(mod.FORMULA_CELLS)
    assert data["K3"] == "=K3-formula"


def test_export_boosts_writes_top_bottom_and_totals(tmp_path: Path) -> None:
    """Нижняя таблица переиспользует те же колонки, что и верхняя (§II.3,
    подтверждено офлайн-снимком живой таблицы — см. docstring модуля)."""
    table: dict[tuple[str, str, str], list[list[object]]] = {}
    for row_num in mod.BOOSTS_TOP_ROWS:
        for name_col, price_col in zip(
            mod.BOOSTS_TOP_NAME_COLS, mod.BOOSTS_TOP_PRICE_COLS, strict=True
        ):
            table[("БУСТЫ", mod.a1_range("БУСТЫ", name_col, row_num), "UNFORMATTED_VALUE")] = [
                ["Уха"]
            ]
            table[("БУСТЫ", mod.a1_range("БУСТЫ", price_col, row_num), "UNFORMATTED_VALUE")] = [
                [6500]
            ]
    for row_num in mod.BOOSTS_BOTTOM_ROWS:
        for name_col, cost_col, qty_col, profit_col in zip(
            mod.BOOSTS_BOTTOM_NAME_COLS,
            mod.BOOSTS_BOTTOM_COST_COLS,
            mod.BOOSTS_BOTTOM_QTY_COLS,
            mod.BOOSTS_BOTTOM_PROFIT_COLS,
            strict=True,
        ):
            table[("БУСТЫ", mod.a1_range("БУСТЫ", name_col, row_num), "UNFORMATTED_VALUE")] = [
                ["Алкобык"]
            ]
            table[("БУСТЫ", mod.a1_range("БУСТЫ", cost_col, row_num), "UNFORMATTED_VALUE")] = [
                [14086]
            ]
            table[("БУСТЫ", mod.a1_range("БУСТЫ", qty_col, row_num), "UNFORMATTED_VALUE")] = [[4]]
            table[("БУСТЫ", mod.a1_range("БУСТЫ", profit_col, row_num), "UNFORMATTED_VALUE")] = [
                [7548]
            ]
    for key, cell in mod.BOOSTS_TOTALS_CELLS.items():
        table[("БУСТЫ", mod.a1_range("БУСТЫ", cell[0], int(cell[1:])), "UNFORMATTED_VALUE")] = [
            [{"total_revenue": 1540000, "total_profit": 530200, "total_cost": 1009800}[key]]
        ]
    reader = FakeRangeReader(table)

    count = mod.export_boosts(reader, tmp_path)
    top_row_count = len(list(mod.BOOSTS_TOP_ROWS)) * len(mod.BOOSTS_TOP_NAME_COLS)
    bottom_row_count = len(list(mod.BOOSTS_BOTTOM_ROWS)) * len(mod.BOOSTS_BOTTOM_NAME_COLS)
    assert count == top_row_count + bottom_row_count

    with (tmp_path / "boosts_top.csv").open(encoding="utf-8") as f:
        top_rows = list(csv.reader(f))
    assert top_rows[0] == ["sheet_row", "group", "name", "price"]
    assert len(top_rows) - 1 == top_row_count

    with (tmp_path / "boosts_bottom.csv").open(encoding="utf-8") as f:
        bottom_rows = list(csv.reader(f))
    assert bottom_rows[0] == ["sheet_row", "group", "name", "cost", "qty", "profit"]
    assert bottom_rows[1] == ["16", "1", "Алкобык", "14086", "4", "7548"]

    totals = json.loads((tmp_path / "boosts_totals.json").read_text(encoding="utf-8"))
    assert totals == {"total_revenue": 1540000, "total_profit": 530200, "total_cost": 1009800}


def test_shelter_export_skipped_without_source(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Без --shelter-id и --shelter-xlsx убежка честно не экспортируется."""
    skupka_reader = FakeRangeReader({})
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(mod, "_skupka_reader", lambda **kwargs: skupka_reader)
        with caplog.at_level("WARNING"):
            volumes = mod.run(
                credentials_path=tmp_path / "creds.json",
                skupka_spreadsheet_id="skupka",
                out_dir=tmp_path,
            )
    assert "shelter_recipes" not in volumes
    assert any("убежка (§II) не экспортирована" in message for message in caplog.messages)


def test_parse_recipe_column_splits_variable_height_blocks() -> None:
    """Разбор реального фрагмента листа «Боеприпасы» (см. docstring модуля):
    заголовок крафта, строка «Предмет», N ингредиентов (включая «Энергия»),
    «Ед. за крафт», «Себестоимость», сразу следующий заголовок — без пустой
    строки-разделителя."""
    rows = [
        ["Гильза", "", "", ""],
        ["Предмет", "Количество", "Цена 1 ед.", "Подсчет для крафта"],
        ["Латунь", 20.0, 108, 0],
        ["Термическая смесь", 2.0, 119, 0],
        ["Энергия", 900.0, 0.86, 0],
        ["Ед. за крафт", 20, "Хочу сделать:", 0.0],
        ["Себестоимость", 159, "Цена крафта", 0],
        ["Крупнокалиберная пуля", "", "", ""],
        ["Предмет", "Количество", "Цена 1 ед.", "Подсчет для крафта"],
        ["Пуля", 20.0, 82, 0],
        ["Ед. за крафт", 20, "Хочу сделать:", 0.0],
        ["Себестоимость", 82, "Цена крафта", 0],
    ]
    recipes = mod._parse_recipe_column(rows, 1)
    assert [r.output for r in recipes] == ["Гильза", "Крупнокалиберная пуля"]

    first = recipes[0]
    assert first.source_row == 1
    assert first.units_per_craft == 20
    assert first.cost == 159
    assert first.ingredients == (
        ("Латунь", 20.0, 108, 1),
        ("Термическая смесь", 2.0, 119, 2),
        ("Энергия", 900.0, 0.86, 3),
    )

    second = recipes[1]
    assert second.source_row == 8
    assert second.units_per_craft == 20
    assert second.cost == 82
    assert second.ingredients == (("Пуля", 20.0, 82, 1),)


def test_read_table_block_pads_short_rows_and_finds_extent() -> None:
    key = ("Цены", "Цены!A3:A5002", "FORMATTED_VALUE")
    reader = FakeRangeReader({key: [["item1"], ["item2"], [""], ["item4"]]})
    reader._table[("Цены", "Цены!A3:D6", "UNFORMATTED_VALUE")] = [
        ["item1", 100, "", 100],
        ["item2", 200],
        [],
        ["item4", 400, 350, 350],
    ]
    rows = mod._read_table_block(reader, "Цены", "A", 3, 4)
    assert rows == [
        [3, "item1", 100, "", 100],
        [4, "item2", 200, "", ""],
        [5, "", "", "", ""],
        [6, "item4", 400, 350, 350],
    ]


def test_export_shelter_settings_stops_at_blank_key(tmp_path: Path) -> None:
    reader = FakeRangeReader(
        {
            ("Инструкция", "Инструкция!C1:D11", "UNFORMATTED_VALUE"): [
                ["Учитывать продажу ингредиентов", False],
                ["Бонус к продаже, %", 0],
                ["Цена 1 ед. энергии, руб", 0.86],
            ]
        }
    )
    count = mod.export_shelter_settings(reader, tmp_path)
    assert count == 3
    with (tmp_path / "shelter_settings.csv").open(encoding="utf-8") as f:
        rows = list(csv.reader(f))
    assert rows[1] == ["1", "Учитывать продажу ингредиентов", "False"]
    assert rows[3] == ["3", "Цена 1 ед. энергии, руб", "0.86"]


def test_export_shelter_recipes_assigns_recipe_seq_for_duplicate_outputs(
    tmp_path: Path,
) -> None:
    """27 предметов убежки крафтятся несколькими рецептами внутри одной
    профессии (§II.2) — `recipe_seq` обязан их различать."""
    left_rows: list[list[object]] = [
        ["Ковёр", "", "", ""],
        ["Предмет", "Количество", "Цена 1 ед.", "Подсчет для крафта"],
        ["Ткань", 2.0, 50, 0],
        ["Ед. за крафт", 1, "Хочу сделать:", 0.0],
        ["Себестоимость", 100, "Цена крафта", 0],
        ["Ковёр", "", "", ""],
        ["Предмет", "Количество", "Цена 1 ед.", "Подсчет для крафта"],
        ["Пряжа", 3.0, 20, 0],
        ["Ед. за крафт", 1, "Хочу сделать:", 0.0],
        ["Себестоимость", 60, "Цена крафта", 0],
    ]
    last_left = 1 + len(left_rows) - 1

    table: dict[tuple[str, str, str], list[list[object]]] = {}
    for _key, sheet in mod.PROFESSION_SHEETS:
        table[(sheet, mod.a1_range(sheet, "A", 1, "A", 5000), "FORMATTED_VALUE")] = (
            [[r[0]] for r in left_rows] if sheet == "Инженерия" else []
        )
        table[(sheet, mod.a1_range(sheet, "F", 1, "F", 5000), "FORMATTED_VALUE")] = []
    table[("Инженерия", mod.a1_range("Инженерия", "A", 1, "D", last_left), "UNFORMATTED_VALUE")] = (
        left_rows
    )
    reader = FakeRangeReader(table)

    mod.export_shelter_recipes(reader, tmp_path)
    with (tmp_path / "shelter_recipes.csv").open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    covers = [r for r in rows if r["output"] == "Ковёр"]
    assert {r["recipe_seq"] for r in covers} == {"1", "2"}
    first = next(r for r in covers if r["recipe_seq"] == "1")
    second = next(r for r in covers if r["recipe_seq"] == "2")
    assert first["ingredient"] == "Ткань"
    assert first["cost"] == "100"
    assert second["ingredient"] == "Пряжа"
    assert second["cost"] == "60"
