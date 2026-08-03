"""Tests for `stalbot.infrastructure.sheets.a1`."""

import pytest

from stalbot.infrastructure.sheets.a1 import (
    ParsedRange,
    a1_range,
    column_index_to_letter,
    column_letter_to_index,
    parse_a1_range,
)


@pytest.mark.parametrize(
    ("index", "letters"),
    [(1, "A"), (2, "B"), (26, "Z"), (27, "AA"), (28, "AB"), (52, "AZ"), (53, "BA"), (702, "ZZ")],
)
def test_column_index_to_letter(index: int, letters: str) -> None:
    assert column_index_to_letter(index) == letters


@pytest.mark.parametrize(
    ("letters", "index"),
    [("A", 1), ("B", 2), ("Z", 26), ("AA", 27), ("AB", 28), ("AZ", 52), ("BA", 53), ("ZZ", 702)],
)
def test_column_letter_to_index(letters: str, index: int) -> None:
    assert column_letter_to_index(letters) == index


def test_column_index_to_letter_rejects_non_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        column_index_to_letter(0)


def test_column_letter_to_index_rejects_lowercase() -> None:
    with pytest.raises(ValueError, match="not a valid column letter"):
        column_letter_to_index("aa")


def test_column_letter_to_index_rejects_empty() -> None:
    with pytest.raises(ValueError):
        column_letter_to_index("")


def test_a1_range_plain_sheet_name_unquoted() -> None:
    assert a1_range("DataBase", "A", 3, "H", 10) == "DataBase!A3:H10"


def test_a1_range_sheet_with_spaces_is_quoted() -> None:
    assert a1_range("Мейн скуп", "C", 1, "D", 31) == "'Мейн скуп'!C1:D31"


def test_a1_range_open_ended_row() -> None:
    assert a1_range("DataBase", "A", 3, "H") == "DataBase!A3:H"


def test_a1_range_single_column_no_row() -> None:
    assert a1_range("DataBase", "F") == "DataBase!F"


def test_parse_a1_range_unquoted_sheet() -> None:
    parsed = parse_a1_range("DataBase!F3:G10")
    assert parsed == ParsedRange(
        sheet="DataBase", col_start="F", row_start=3, col_end="G", row_end=10
    )


def test_parse_a1_range_quoted_sheet_with_spaces() -> None:
    parsed = parse_a1_range("'Мейн скуп'!C1:D31")
    assert parsed.sheet == "Мейн скуп"
    assert parsed.col_start == "C"
    assert parsed.col_end == "D"


def test_parse_a1_range_single_cell_defaults_col_end_to_col_start() -> None:
    parsed = parse_a1_range("DataBase!F3")
    assert parsed.col_start == "F"
    assert parsed.col_end == "F"
    assert parsed.row_start == 3
    assert parsed.row_end is None


def test_parse_a1_range_open_ended_row() -> None:
    parsed = parse_a1_range("DataBase!A3:H")
    assert parsed.row_start == 3
    assert parsed.row_end is None
    assert parsed.col_end == "H"


def test_parse_a1_range_rejects_malformed_ref() -> None:
    with pytest.raises(ValueError, match="not a valid sheet-qualified A1 range"):
        parse_a1_range("not a range")


def test_a1_range_and_parse_a1_range_round_trip() -> None:
    ref = a1_range("DataBase", "I", 3, "S", 500)
    parsed = parse_a1_range(ref)
    assert (parsed.sheet, parsed.col_start, parsed.row_start, parsed.col_end, parsed.row_end) == (
        "DataBase",
        "I",
        3,
        "S",
        500,
    )
