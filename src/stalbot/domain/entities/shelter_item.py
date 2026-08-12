"""`ShelterItem` — a row of the `shelter_items` table.

sqlite_migration.md §IV.3, Э5. The game reference catalog — 421 items,
distinct from `catalog_items` (199 names, what the bot actually trades).
Bridged by `catalog_items.shelter_item_id`.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class ShelterItem:
    """One entry in the game's crafting/pricing reference."""

    id: int | None
    """`None` for a not-yet-persisted item — the repository assigns one on insert."""
    name: str
    name_norm: str
    kind: str
    """`component` | `craftable` | `virtual` (Энергия is the only `virtual` row)."""
    market_kopeks: int | None
    """«Средняя цена»/«Себестоимость» column — the workbook's own estimate."""
    my_kopeks: int | None
    """«Моя цена» — a manual override that wins over any recipe (§V.2)."""
    vendor_kopeks: int | None
    """Тех. лист «Цена продажи» — vendor sell price, informational only."""
    updated_at: datetime | None = None
