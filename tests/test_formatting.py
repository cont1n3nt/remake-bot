"""Тесты единого форматирования чисел (bot/utils/formatting.py).

Раньше этот файл фиксировал РАСХОЖДЕНИЕ четырёх независимых реализаций
(`ocr_service._fmt`, `admin_cmds._fmt`, `analytics._fmt`, `embeds._fmt_thousands`,
`transactions._amount_str`) — числа в /profile выглядели иначе, чем в /logs.
После унификации (пункт 21 списка багов) все они — обёртки над `format_amount`,
и тесты проверяют ровно обратное: что все пять дают одинаковый результат.
"""
import math

import pytest

from bot.utils.formatting import format_amount
from bot.services.ocr_service import _fmt as ocr_fmt
from bot.utils.embeds import _fmt_thousands as embeds_fmt
from bot.cogs.admin_cmds import _fmt as admin_fmt
from bot.cogs.analytics import _fmt as analytics_fmt
from bot.cogs.transactions import _amount_str

ALL_FORMATTERS = (format_amount, ocr_fmt, embeds_fmt, admin_fmt, analytics_fmt, _amount_str)

CASES = [
    (0, "0"),
    (0.0, "0"),
    (1, "1"),
    (999, "999"),
    (1000, "1 000"),
    (10000, "10 000"),
    (100000, "100 000"),
    (1000000, "1 000 000"),
    (1500000.0, "1 500 000"),
    (128413100, "128 413 100"),
    (1500000.5, "1 500 000.5"),
    (1500000.567, "1 500 000.57"),   # округление до 2 знаков
    (1500000.50, "1 500 000.5"),     # хвостовой ноль убирается
    (-5.0, "-5"),
    (-5.25, "-5.25"),
    (-1500000.0, "-1 500 000"),
    (0.1, "0.1"),
    (100.1, "100.1"),
]


@pytest.mark.parametrize("value,expected", CASES)
def test_format_amount(value, expected):
    assert format_amount(value) == expected


@pytest.mark.parametrize("value,expected", CASES)
def test_all_formatters_agree(value, expected):
    """Ни одна обёртка не должна отличаться от единого форматтера."""
    for fn in ALL_FORMATTERS:
        assert fn(value) == expected, f"{fn.__module__}.{fn.__name__} разошлась"


def test_thousands_separator_is_plain_space():
    # Разделитель — обычный пробел U+0020: Discord его не схлопывает внутри
    # значения поля эмбеда, а копипаста числа обратно в таблицу остаётся рабочей.
    assert format_amount(1000000) == "1 000 000"
    assert " " not in format_amount(1000000)


def test_non_finite_does_not_crash():
    # Раньше падало на int(n) во всех версиях, кроме transactions._amount_str.
    for fn in ALL_FORMATTERS:
        assert fn(float("nan")) == "nan"
        assert fn(float("inf")) == "inf"
        assert fn(float("-inf")) == "-inf"
    assert math.isnan(float(format_amount(float("nan"))))


def test_int_and_float_of_same_value_match():
    assert format_amount(1000) == format_amount(1000.0)
