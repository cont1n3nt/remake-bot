"""Baseline-фиксирующие тесты для bot/utils/calculator.py::safe_calc.

Эти тесты не проверяют "правильность" поведения — они фиксируют текущий
выход функции как эталон (см. REFACTORING_PLAN.md, Этап 0.1), чтобы
последующие этапы рефакторинга могли сравниться с ним.
"""
import pytest

from bot.utils.calculator import safe_calc


def test_simple_expression():
    assert safe_calc("1500*3+200") == 4700.0


def test_k_suffix_latin():
    assert safe_calc("5k") == 5000.0


def test_k_suffix_cyrillic():
    assert safe_calc("5к") == 5000.0


def test_k_suffix_with_decimal():
    assert safe_calc("2.5k") == 2500.0


def test_negative_k_suffix():
    assert safe_calc("-5k") == -5000.0


def test_empty_string_raises():
    with pytest.raises(ValueError):
        safe_calc("")


def test_whitespace_only_raises():
    with pytest.raises(ValueError):
        safe_calc("   ")


def test_garbage_input_raises():
    with pytest.raises(ValueError):
        safe_calc("abc")


def test_letters_are_silently_stripped():
    # Текущее поведение: буквы вырезаются регэкспом до вычисления,
    # "1abc2" превращается в "12". Задокументировано как есть (AUDIT.md §7.3).
    assert safe_calc("1abc2") == 12.0


def test_negative_number():
    assert safe_calc("-100") == -100.0


def test_comma_as_decimal_separator():
    assert safe_calc("1,5") == 1.5


def test_zero():
    assert safe_calc("0") == 0.0


def test_division_by_zero_raises_value_error_via_fallback():
    # simpleeval кидает ZeroDivisionError, он ловится общим except Exception,
    # после чего происходит fallback на float("1/0"), который кидает
    # стандартный ValueError. Сообщение отличается от кастомных ValueError
    # выше ("Сумма не может быть пустой" и т.п.) — это тоже часть текущего
    # поведения, а не унифицированная ошибка.
    with pytest.raises(ValueError):
        safe_calc("1/0")


def test_huge_product_raises_overflow_error_not_value_error():
    # ЛОВУШКА: safe_calc объявляет свой контракт как "кидает ValueError",
    # но для достаточно большого произведения int получившийся результат не
    # помещается в float, и `float(result)` на строке после try/except
    # (вне try) кидает OverflowError, а не ValueError. Вызывающий код,
    # ловящий только ValueError, такое исключение не поймает.
    expr = "*".join(["9999999999"] * 40)
    with pytest.raises(OverflowError):
        safe_calc(expr)
