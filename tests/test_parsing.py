"""Тесты для bot/utils/parsing.py::parse_ruble_amount — единая замена
дословно дублировавшихся bot/cogs/admin_cmds.py::_parse_amount_logs и
bot/cogs/analytics.py::_parse_float (REFACTORING_PLAN.md, Этап C.2).

Не путать с SheetsRepository._parse_float — та работает с другим набором
данных (сырые значения из Google Sheets API, без символа ₽) и в этот
этап не входит.
"""
from bot.utils.parsing import parse_ruble_amount


def test_none_returns_zero():
    assert parse_ruble_amount(None) == 0.0


def test_int_passthrough():
    assert parse_ruble_amount(1500) == 1500.0


def test_float_passthrough():
    assert parse_ruble_amount(1500.5) == 1500.5


def test_plain_digit_string():
    assert parse_ruble_amount("1500") == 1500.0


def test_strips_spaces():
    assert parse_ruble_amount("1 500") == 1500.0


def test_comma_as_decimal_separator():
    assert parse_ruble_amount("1,5") == 1.5


def test_strips_ruble_sign():
    assert parse_ruble_amount("1500₽") == 1500.0


def test_combined_spaces_comma_and_ruble_sign():
    assert parse_ruble_amount("  1 500,50 ₽ ") == 1500.50


def test_garbage_returns_zero():
    assert parse_ruble_amount("abc") == 0.0


def test_empty_string_returns_zero():
    assert parse_ruble_amount("") == 0.0
