"""A1 notation helpers: column letters <-> indexes, range building/parsing.

Every other Sheets module talks in A1 strings (`"DataBase!F3:G10"`) rather
than raw row/column pairs, because that is what the Sheets API itself takes
and returns. This module is the single place that knows how to build and
take those strings apart.
"""

import re
from dataclasses import dataclass
from typing import Final

_COLUMN_RE: Final = re.compile(r"^[A-Z]+$")
#: Unquoted sheet names may be any run of characters without a space, `!` or
#: `'` (Sheets only requires quoting for those) — this covers the project's
#: Cyrillic sheet titles ("БУСТЫ") as readily as "DataBase".
_RANGE_RE: Final = re.compile(
    r"^(?:'(?P<quoted_sheet>[^']+)'|(?P<sheet>[^\s!']+))!"
    r"(?P<col_start>[A-Z]+)(?P<row_start>\d+)?"
    r"(?::(?P<col_end>[A-Z]+)(?P<row_end>\d+)?)?$"
)
_NEEDS_QUOTING_RE: Final = re.compile(r"[\s!']")


def column_index_to_letter(index: int) -> str:
    """Convert a 1-based column index to its A1 letter (1 -> "A", 27 -> "AA").

    Args:
        index: A 1-based column index.

    Returns:
        The column letters.

    Raises:
        ValueError: If `index` is not positive.
    """
    if index < 1:
        raise ValueError(f"column index must be positive, got {index}")
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def column_letter_to_index(letters: str) -> int:
    """Convert an A1 column letter to its 1-based index ("A" -> 1, "AA" -> 27).

    Args:
        letters: Upper-case column letters.

    Returns:
        The 1-based column index.

    Raises:
        ValueError: If `letters` is not a run of A-Z characters.
    """
    if not _COLUMN_RE.match(letters):
        raise ValueError(f"not a valid column letter: {letters!r}")
    index = 0
    for char in letters:
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index


def a1_range(
    sheet: str,
    col_start: str,
    row_start: int | None = None,
    col_end: str | None = None,
    row_end: int | None = None,
) -> str:
    """Build an A1 range string, quoting the sheet name when it has spaces.

    Args:
        sheet: Worksheet title, exactly as it appears in the spreadsheet.
        col_start: First column letters.
        row_start: First row (1-based). Omitted for an open-ended column range.
        col_end: Last column letters. Omitted for a single column.
        row_end: Last row. Omitted for an open-ended range.

    Returns:
        An A1 range string such as `"'Мейн скуп'!C1:D31"`.
    """
    sheet_ref = f"'{sheet}'" if _NEEDS_QUOTING_RE.search(sheet) else sheet
    start = f"{col_start}{row_start}" if row_start is not None else col_start
    if col_end is None:
        return f"{sheet_ref}!{start}"
    end = f"{col_end}{row_end}" if row_end is not None else col_end
    return f"{sheet_ref}!{start}:{end}"


@dataclass(frozen=True, slots=True)
class ParsedRange:
    """A decomposed A1 range: sheet name plus its column/row bounds."""

    sheet: str
    col_start: str
    row_start: int | None
    col_end: str
    row_end: int | None


def parse_a1_range(ref: str) -> ParsedRange:
    """Decompose an A1 range string into its sheet and column/row bounds.

    Args:
        ref: An A1 range string, e.g. `"DataBase!F3:G10"` or `"'Мейн скуп'!C1"`.

    Returns:
        The parsed sheet name and bounds. A single-cell reference has
        `col_end == col_start`; an open-ended range has `row_start`/`row_end`
        as `None`.

    Raises:
        ValueError: If `ref` is not a well-formed sheet-qualified A1 range.
    """
    match = _RANGE_RE.match(ref)
    if match is None:
        raise ValueError(f"not a valid sheet-qualified A1 range: {ref!r}")
    sheet = match.group("quoted_sheet") or match.group("sheet")
    row_start = match.group("row_start")
    col_end = match.group("col_end") or match.group("col_start")
    row_end = match.group("row_end")
    return ParsedRange(
        sheet=sheet,
        col_start=match.group("col_start"),
        row_start=int(row_start) if row_start is not None else None,
        col_end=col_end,
        row_end=int(row_end) if row_end is not None else None,
    )
