"""Tests for `stalbot.domain.money` (PLAN.md §5.1, §13)."""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from stalbot.domain.errors import AmountParseError
from stalbot.domain.money import (
    evaluate_amount,
    format_amount,
    format_compact,
    format_kopeks,
    from_storage,
    parse_amount,
    to_storage,
)

# --- parse_amount: plain integers, spacing, underscores ---------------------

PARSE_AMOUNT_CASES: list[tuple[str, Decimal]] = [
    ("0", Decimal(0)),
    ("1", Decimal(1)),
    ("999", Decimal(999)),
    ("1000", Decimal(1000)),
    ("10 000", Decimal(10_000)),
    ("100 000", Decimal(100_000)),
    ("1 000 000", Decimal(1_000_000)),
    ("10 000 000", Decimal(10_000_000)),
    ("1 500 000", Decimal(1_500_000)),
    ("1_000_000", Decimal(1_000_000)),
    ("1_500_000", Decimal(1_500_000)),
    ("007", Decimal(7)),
    ("00299900", Decimal(299_900)),
    ("  299900  ", Decimal(299_900)),
    ("10 000", Decimal(10_000)),  # narrow no-break space
    ("10 000", Decimal(10_000)),  # no-break space
    # --- currency markers, either edge, case-insensitive ---
    ("299900₽", Decimal(299_900)),
    ("₽299900", Decimal(299_900)),
    ("299 900 ₽", Decimal(299_900)),
    ("299900руб", Decimal(299_900)),
    ("299900Руб", Decimal(299_900)),
    ("299900руб.", Decimal(299_900)),
    ("1 500 000 р.", Decimal(1_500_000)),
    ("1 500 000 Р.", Decimal(1_500_000)),
    ("299900rub", Decimal(299_900)),
    ("299900RUB", Decimal(299_900)),
    ("$100", Decimal(100)),
    ("100$", Decimal(100)),
    ("€100", Decimal(100)),
    ("100€", Decimal(100)),
    ("100P", Decimal(100)),
    ("100p", Decimal(100)),
    ("100 р", Decimal(100)),
    # --- decimal comma vs thousands comma ---
    ("1,5", Decimal("1.5")),
    ("1,50", Decimal("1.5")),
    ("0,5", Decimal("0.5")),
    ("1,500", Decimal(1_500)),  # ambiguous -> resolved as thousands per PLAN.md
    ("10,000", Decimal(10_000)),
    ("299,900", Decimal(299_900)),
    ("1,500,000", Decimal(1_500_000)),
    ("1.5", Decimal("1.5")),
    ("0.5", Decimal("0.5")),
    # --- multiplier suffixes: thousand ---
    ("10к", Decimal(10_000)),
    ("10К", Decimal(10_000)),
    ("10k", Decimal(10_000)),
    ("10K", Decimal(10_000)),
    ("250k", Decimal(250_000)),
    ("1.5к", Decimal(1_500)),
    # --- multiplier suffixes: million ---
    ("1,5кк", Decimal(1_500_000)),
    ("1.5кк", Decimal(1_500_000)),
    ("1.5kk", Decimal(1_500_000)),
    ("1.5KK", Decimal(1_500_000)),
    ("1.5m", Decimal(1_500_000)),
    ("1.5м", Decimal(1_500_000)),
    ("2кк", Decimal(2_000_000)),
    # --- multiplier suffixes: billion ---
    ("3ккк", Decimal(3_000_000_000)),
    ("3kkk", Decimal(3_000_000_000)),
    ("3b", Decimal(3_000_000_000)),
    ("1.5ккк", Decimal("1500000000")),
    # --- currency + multiplier combined ---
    ("300кр", Decimal(300_000)),
    ("300к₽", Decimal(300_000)),
    ("250к₽", Decimal(250_000)),
    ("1.5ккруб", Decimal(1_500_000)),
]


@pytest.mark.parametrize(("raw", "expected"), PARSE_AMOUNT_CASES)
def test_parse_amount(raw: str, expected: Decimal) -> None:
    assert parse_amount(raw) == expected


PARSE_AMOUNT_ERROR_CASES: list[str] = [
    "",
    "   ",
    "abc",
    "три тысячи",
    "10 20 30",  # no operator, ambiguous garbage
    "1,50,000",  # malformed grouping
    "x" * 300,  # too long
    "-",
    "..",
]


@pytest.mark.parametrize("raw", PARSE_AMOUNT_ERROR_CASES)
def test_parse_amount_rejects_invalid_input(raw: str) -> None:
    with pytest.raises(AmountParseError):
        parse_amount(raw)


# --- evaluate_amount: arithmetic ---------------------------------------

EVALUATE_AMOUNT_CASES: list[tuple[str, Decimal]] = [
    ("299 900 ₽ + 10000", Decimal(309_900)),
    ("1.5кк * 3 - 250к", Decimal(4_250_000)),
    ("100 + 50", Decimal(150)),
    ("100 - 50", Decimal(50)),
    ("100 * 3", Decimal(300)),
    ("100 / 4", Decimal(25)),
    ("100 // 30", Decimal(3)),
    ("100 % 30", Decimal(10)),
    ("2 ** 3", Decimal(8)),
    ("(100 + 50) * 2", Decimal(300)),
    ("-100 + 50", Decimal(-50)),
    ("+100", Decimal(100)),
    ("10к + 5к", Decimal(15_000)),
    ("1кк - 1", Decimal(999_999)),
    ("250000", Decimal(250_000)),
    ("(10к + 5к) * 2", Decimal(30_000)),
]


@pytest.mark.parametrize(("expression", "expected"), EVALUATE_AMOUNT_CASES)
def test_evaluate_amount(expression: str, expected: Decimal) -> None:
    assert evaluate_amount(expression) == expected


EVALUATE_AMOUNT_ERROR_CASES: list[str] = [
    "",
    "abc",
    "__import__('os')",
    "os.system('ls')",
    "[1, 2, 3]",
    "1,50,000",
    "2 ** 9999",
    "2 ** (1 + 1)",  # exponent must be a plain integer literal, not an expression
    "1 / 0",
    "1 +",  # incomplete expression -> ast.parse SyntaxError, not a number-parsing error
    "-" * 40 + "1",  # 40 nested UnaryOp nodes -> exceeds the AST depth guard
    "x" * 300,
    "lambda: 1",
    # SEC-1: Python's native grammar recognizes scientific notation as a
    # float literal, unlike parse_amount's regex-based path — a big enough
    # one overflows silently to `inf`/`-inf` instead of raising SyntaxError.
    "1e400",
    "1e309",
    "1e400 - 1e400",
    "-1e400",
]


@pytest.mark.parametrize("expression", EVALUATE_AMOUNT_ERROR_CASES)
def test_evaluate_amount_rejects_invalid_input(expression: str) -> None:
    with pytest.raises(AmountParseError):
        evaluate_amount(expression)


# --- SEC-1: non-finite/overflowing results must raise, never leak an -------
# --- internal decimal.DecimalException past the domain boundary. ----------


def test_evaluate_amount_rejects_scientific_notation_that_overflows_to_infinity() -> None:
    with pytest.raises(AmountParseError):
        evaluate_amount("1e400")


def test_evaluate_amount_allows_scientific_notation_that_stays_finite() -> None:
    """Only non-finite results are rejected — scientific notation itself is fine."""
    assert evaluate_amount("1e10") == Decimal("1e10")


def test_evaluate_amount_rejects_a_decimal_overflow_from_legitimate_arithmetic() -> None:
    """A handful of nested `** 12` terms, each within `_MAX_POWER_EXPONENT`, can
    still overflow the context's exponent range — must not leak `decimal.Overflow`."""
    expression = "2"
    for _ in range(7):
        expression = f"({expression}**12)"
    with pytest.raises(AmountParseError):
        evaluate_amount(expression)


# --- DOM-1: no float round-trip for high-precision fractional amounts -------


def test_evaluate_amount_preserves_precision_above_float64_range() -> None:
    """A fractional literal Python's float grammar cannot round-trip exactly."""
    assert evaluate_amount("999999999999999.99") == Decimal("999999999999999.99")


def test_evaluate_amount_preserves_precision_in_arithmetic() -> None:
    assert evaluate_amount("999999999999999.99 + 0.01") == Decimal("1000000000000000.00")


def test_evaluate_amount_fractional_multiplier_stays_exact() -> None:
    assert evaluate_amount("1.5кк * 3 - 250к") == Decimal(4_250_000)


# --- DOM-3: no silent rounding above the ambient 28-digit decimal context ---


def test_parse_amount_preserves_precision_above_28_significant_digits() -> None:
    """The default decimal context caps arithmetic at 28 sig figs; a multiplier
    multiplication would otherwise silently round a longer amount."""
    assert parse_amount("123456789012345678901234567.89ккк") == Decimal(
        "123456789012345678901234567890000000.00"
    )


def test_evaluate_amount_preserves_precision_above_28_significant_digits() -> None:
    assert evaluate_amount("123456789012345678901234567890 + 1") == Decimal(
        "123456789012345678901234567891"
    )


# --- format_amount / format_compact -----------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal(0), "0 ₽"),
        (Decimal(999), "999 ₽"),
        (Decimal(1000), "1 000 ₽"),
        (Decimal(299_900), "299 900 ₽"),
        (Decimal(1_500_000), "1 500 000 ₽"),
    ],
)
def test_format_amount_with_currency(value: Decimal, expected: str) -> None:
    assert format_amount(value) == expected.replace(" ", " ")


def test_format_kopeks_keeps_the_sub_ruble_remainder() -> None:
    assert format_kopeks(186).startswith("1,86")


def test_format_kopeks_pads_a_single_digit_remainder() -> None:
    assert format_kopeks(105).startswith("1,05")


def test_format_kopeks_groups_thousands() -> None:
    text = format_kopeks(123_456_789)
    assert text.endswith("567,89 ₽")
    assert "234" in text


def test_format_kopeks_zero() -> None:
    assert format_kopeks(0).startswith("0,00")


def test_format_amount_without_currency() -> None:
    assert format_amount(Decimal(299_900), currency=False) == f"299{' '}900"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal(500), "500"),
        (Decimal(1000), "1 к"),
        (Decimal(1_500), "1.5 к"),
        (Decimal(1_500_000), "1.5 кк"),
        (Decimal(3_000_000), "3 кк"),
        (Decimal(3_000_000_000), "3 ккк"),
    ],
)
def test_format_compact(value: Decimal, expected: str) -> None:
    assert format_compact(value) == expected


def test_format_compact_negative() -> None:
    assert format_compact(Decimal(-1_500_000)) == "-1.5 кк"


# --- DOM-4: escalate to the next unit when rounding reaches 1000 -----------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal(999_950), "1 кк"),  # 999.95к rounds to 1000.0к -> escalate, not "1000 к"
        (Decimal(999_949), "999.9 к"),  # just below the boundary: no escalation
        (Decimal(999_950_000), "1 ккк"),  # same boundary one tier up: кк -> ккк
    ],
)
def test_format_compact_escalates_unit_on_rounding_boundary(value: Decimal, expected: str) -> None:
    assert format_compact(value) == expected


# --- property: parse_amount(format_amount(x)) == x ----------------------


@given(st.integers(min_value=0, max_value=10**12))
def test_parse_format_round_trip(value: int) -> None:
    assert parse_amount(format_amount(Decimal(value))) == Decimal(value)


# --- to_storage / from_storage: the Rub storage boundary (sqlite_migration.md §III.3) ---


def test_to_storage_rounds_half_up_like_round_for_storage() -> None:
    assert to_storage(Decimal("100.5")) == 101
    assert to_storage(Decimal("100.4")) == 100
    assert to_storage(Decimal(100)) == 100


def test_to_storage_returns_a_plain_int_subtype() -> None:
    stored = to_storage(Decimal("1500000"))
    assert isinstance(stored, int)
    assert stored == 1_500_000


def test_from_storage_returns_a_decimal() -> None:
    assert from_storage(1_500_000) == Decimal(1_500_000)


@given(st.integers(min_value=-(10**12), max_value=10**12))
def test_to_storage_from_storage_round_trip_on_whole_values(value: int) -> None:
    assert to_storage(from_storage(value)) == value
