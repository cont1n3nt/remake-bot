"""Game nick normalization (see PLAN.md §6.3).

Every nick match in the project — profile lookup, referral binding, stats,
Discord binding — goes through the normalized form; the original spelling is
kept separately for display.
"""

from typing import NewType

#: A nick that has been through `normalize_nick`; safe to use as a lookup key.
NormalizedNick = NewType("NormalizedNick", str)


def normalize_nick(raw: str) -> NormalizedNick:
    """Normalize a game nick to its canonical lookup form.

    Trims surrounding whitespace, collapses internal whitespace runs to a
    single space, and lower-cases the result with the same semantics as the
    sheet's own `UNIQUE(ARRAYFORMULA(LOWER(B3:B)))` formula — `lower()`
    rather than `casefold()`, so this stays byte-identical to what the
    spreadsheet groups together.

    Args:
        raw: The nick as typed by a player or admin.

    Returns:
        The canonical form.
    """
    return NormalizedNick(" ".join(raw.split()).lower())
