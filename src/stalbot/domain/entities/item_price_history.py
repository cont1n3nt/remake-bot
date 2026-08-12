"""`ItemPriceHistoryEntry` — a row of the `item_price_history` table.

sqlite_migration.md §IV.2, Э7.
"""

from dataclasses import dataclass
from datetime import datetime

from stalbot.domain.enums import PriceChangeSource, PriceField
from stalbot.domain.money import Rub


@dataclass(frozen=True, slots=True)
class ItemPriceHistoryEntry:
    """One recorded change to a catalog item's buy or sell price."""

    id: int | None
    """`None` for a not-yet-persisted entry — the repository assigns one on insert."""
    item_id: int
    field: PriceField
    old_price: Rub | None
    new_price: Rub | None
    changed_by: int | None
    """Discord id of whoever made the change, or `None` (e.g. an import with no single actor)."""
    source: PriceChangeSource
    changed_at: datetime
