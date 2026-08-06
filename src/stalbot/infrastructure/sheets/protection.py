"""Formula-column write protection (see PLAN.md §7.3).

The customer's decision is absolute: the bot never writes a Sheets formula,
ever. `ensure_writable` is the single choke point that enforces it — every
write in `SheetsClient.batch_update` goes through it *before* any network
call, so a bug can only fail loudly and locally, never corrupt a formula.
"""

from typing import Final

from stalbot.domain.errors import ProtectedRangeWriteError
from stalbot.infrastructure.sheets.a1 import (
    column_index_to_letter,
    column_letter_to_index,
    parse_a1_range,
)

#: The only sheet whose columns are formula-protected. Other sheets (price
#: lists) reuse the same letters for unrelated, freely writable columns.
DATABASE_SHEET: Final = "DataBase"

#: Formula-owned columns on `DataBase` (PLAN.md §6.1): `F`/`G` in the Тикеты
#: block, `J,K,L,M,N,O,P,R,S` in the Юзеры block. Everything else the bot
#: touches there (`A-E,H`, `I,Q`, `AA:AG`) is raw data it owns.
READ_ONLY_COLUMNS: Final[frozenset[str]] = frozenset(
    {"F", "G", "J", "K", "L", "M", "N", "O", "P", "R", "S"}
)


def ensure_writable(ref: str) -> None:
    """Raise if an A1 range write would touch a formula-owned column.

    Args:
        ref: A sheet-qualified A1 range, e.g. `"DataBase!A10:H10"`.

    Raises:
        ProtectedRangeWriteError: If any column the range spans is
            formula-owned on the `DataBase` sheet.
        ValueError: If `ref`'s end column comes before its start column —
            an inverted range would otherwise make `range(start, end + 1)`
            empty and skip every column check (fail loudly instead).
    """
    parsed = parse_a1_range(ref)
    if parsed.sheet != DATABASE_SHEET:
        return
    start = column_letter_to_index(parsed.col_start)
    end = column_letter_to_index(parsed.col_end)
    if end < start:
        raise ValueError(f"inverted column range: {ref!r} (end column before start column)")
    for index in range(start, end + 1):
        letter = column_index_to_letter(index)
        if letter in READ_ONLY_COLUMNS:
            raise ProtectedRangeWriteError(
                f"column {letter} on sheet {DATABASE_SHEET!r} is formula-owned "
                f"and read-only (attempted write to {ref!r})"
            )
