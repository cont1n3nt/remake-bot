"""Baseline-фиксирующие тесты для пяти независимых копий форматирования чисел
(AUDIT.md §7.1 п.2): ocr_service._fmt, embeds._fmt, admin_cmds._fmt,
analytics._fmt, transactions._amount_str.

Один и тот же набор входов прогоняется через все пять функций, чтобы явно
зафиксировать тестами уже существующее расхождение поведения — это НЕ
унификация, а документирование текущего состояния (см. REFACTORING_PLAN.md,
Этап 0.1 и Этап C.1/C.3).
"""
import math

from bot.services.ocr_service import _fmt as ocr_fmt
from bot.utils.embeds import _fmt as embeds_fmt
from bot.cogs.admin_cmds import _fmt as admin_fmt
from bot.cogs.analytics import _fmt as analytics_fmt
from bot.cogs.transactions import _amount_str


# --- ocr_service._fmt / admin_cmds._fmt / analytics._fmt -------------------
# Дословно идентичные реализации (побайтово сверено при аудите и здесь же
# перепроверено одинаковыми ожидаемыми значениями).

IDENTICAL_FMT_CASES = [
    (0.0, "0"),
    (1.0, "1"),
    (1500000.0, "1500000"),
    (1500000.5, "1500000.5"),
    (1500000.567, "1500000.57"),  # округление до .2f — теряет точность
    (-5.0, "-5"),
    (-5.25, "-5.25"),
    (0.1, "0.1"),
    (100.1, "100.1"),
]


def test_ocr_fmt_matches_admin_and_analytics_fmt():
    for value, expected in IDENTICAL_FMT_CASES:
        assert ocr_fmt(value) == expected
        assert admin_fmt(value) == expected
        assert analytics_fmt(value) == expected


def test_identical_fmt_family_crashes_on_non_finite_input():
    # Ни одна из трёх идентичных _fmt не проверяет math.isfinite — они
    # безусловно вызывают int(n) для сравнения n == int(n), что кидает
    # исключение на NaN/inf. Задокументировано как есть, не исправлено.
    for fn in (ocr_fmt, admin_fmt, analytics_fmt):
        try:
            fn(float("nan"))
            assert False, "ожидалось исключение на NaN"
        except ValueError:
            pass
        try:
            fn(float("inf"))
            assert False, "ожидалось исключение на inf"
        except OverflowError:
            pass


# --- embeds._fmt -------------------------------------------------------
# Единственная версия, добавляющая разделители тысяч (пробел). Числа в
# /profile выглядят иначе, чем в /logs, /day, /week, /month.

EMBEDS_FMT_CASES = [
    (0.0, "0"),
    (1.0, "1"),
    (1500000.0, "1 500 000"),
    (1500000.5, "1 500 000.5"),
    (1500000.567, "1 500 000.567"),  # без округления, в отличие от ocr/admin/analytics
    (-5.0, "-5"),
    (-5.25, "-5.25"),
    (0.1, "0.1"),
    (100.1, "100.1"),
]


def test_embeds_fmt_adds_thousands_separator():
    for value, expected in EMBEDS_FMT_CASES:
        assert embeds_fmt(value) == expected


def test_embeds_fmt_diverges_from_identical_fmt_family_on_fraction():
    # 1500000.567 форматируется по-разному: embeds._fmt не округляет
    # дробную часть до двух знаков, остальные три _fmt — округляют.
    assert embeds_fmt(1500000.567) == "1 500 000.567"
    assert ocr_fmt(1500000.567) == "1500000.57"


def test_embeds_fmt_crashes_on_non_finite_input():
    try:
        embeds_fmt(float("nan"))
        assert False, "ожидалось исключение на NaN"
    except ValueError:
        pass
    try:
        embeds_fmt(float("inf"))
        assert False, "ожидалось исключение на inf"
    except OverflowError:
        pass


# --- transactions._amount_str -------------------------------------------
# Единственная версия, которая (а) не округляет дробную часть и
# (б) явно обрабатывает non-finite через math.isfinite вместо падения.
# Сознательно НЕ объединяется с остальными _fmt (см. Этап C.3 плана) —
# это фиксирует поведение, а не готовит его унификацию.

AMOUNT_STR_CASES = [
    (0.0, "0"),
    (1.0, "1"),
    (1500000.0, "1500000"),
    (1500000.5, "1500000.5"),
    (1500000.567, "1500000.567"),  # НЕ округляется, в отличие от _fmt-семейства
    (-5.0, "-5"),
    (-5.25, "-5.25"),
    (0.1, "0.1"),
    (100.1, "100.1"),
]


def test_amount_str_does_not_round_fraction():
    for value, expected in AMOUNT_STR_CASES:
        assert _amount_str(value) == expected


def test_amount_str_handles_non_finite_gracefully():
    # В отличие от всех четырёх _fmt-версий выше, _amount_str не падает —
    # она явно проверяет math.isfinite() и возвращает str(amount) как есть.
    assert _amount_str(float("nan")) == "nan"
    assert _amount_str(float("inf")) == "inf"
    assert math.isnan(float(_amount_str(float("nan"))))
