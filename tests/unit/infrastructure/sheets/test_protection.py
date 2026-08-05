"""Tests for `stalbot.infrastructure.sheets.protection` (PLAN.md §7.3, §16).

The parametrized sweep over every `DataBase` column is the "перебрать все
места записи" contract test M2 calls for: it pins down, letter by letter,
exactly which columns are formula-owned.
"""

import pytest

from stalbot.domain.errors import ProtectedRangeWriteError
from stalbot.infrastructure.sheets.protection import READ_ONLY_COLUMNS, ensure_writable

#: Every column letter in the four `DataBase` blocks (PLAN.md §6.1):
#: Тикеты A-H, Юзеры I-S, Магазин U-V, item database AA-AG.
_ALL_DATABASE_COLUMNS = (
    *"ABCDEFGH",
    *"IJKLMNOPQRS",
    "U",
    "V",
    *("AA", "AB", "AC", "AD", "AE", "AF", "AG"),
)


@pytest.mark.parametrize("column", sorted(READ_ONLY_COLUMNS))
def test_single_read_only_column_is_rejected(column: str) -> None:
    with pytest.raises(ProtectedRangeWriteError, match=column):
        ensure_writable(f"DataBase!{column}3")


@pytest.mark.parametrize("column", [c for c in _ALL_DATABASE_COLUMNS if c not in READ_ONLY_COLUMNS])
def test_writable_database_column_is_accepted(column: str) -> None:
    ensure_writable(f"DataBase!{column}3")  # must not raise


def test_range_spanning_a_writable_and_a_protected_column_is_rejected() -> None:
    with pytest.raises(ProtectedRangeWriteError):
        ensure_writable("DataBase!E3:G3")  # E writable, F/G formulas


def test_ticket_block_write_range_is_accepted() -> None:
    ensure_writable("DataBase!A10:E10")


def test_users_block_raw_columns_are_accepted() -> None:
    ensure_writable("DataBase!I3")
    ensure_writable("DataBase!Q3")


def test_item_database_block_is_fully_writable() -> None:
    ensure_writable("DataBase!AA3:AG3")


@pytest.mark.parametrize("column", sorted(READ_ONLY_COLUMNS))
def test_same_column_letters_are_writable_on_other_sheets(column: str) -> None:
    """Protection is scoped to `DataBase` only — price sheets reuse these letters freely."""
    ensure_writable(f"'Мейн скуп'!{column}3")
    ensure_writable(f"БУСТЫ!{column}3")


def test_read_only_columns_match_plan_spec() -> None:
    assert READ_ONLY_COLUMNS == frozenset({"F", "G", "J", "K", "L", "M", "N", "O", "P", "R", "S"})


def test_inverted_range_is_rejected_instead_of_skipping_checks() -> None:
    """`range(start, end + 1)` is silently empty when end < start (fail-open) — must not be."""
    with pytest.raises(ValueError, match="inverted"):
        ensure_writable("DataBase!G3:F3")  # G(7) before F(6): reversed
