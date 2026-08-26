"""`CatalogItem` — a row of the `catalog_items` table.

sqlite_migration.md §IV.2, Э3/Э4. Not `Item` (`domain/entities/item.py`) —
that entity backs the legacy Sheets-cache `items` table, which keeps
serving the live bot until Э6/Э7. The two coexist on purpose (§X: "снос
легаси-таблиц" is a later migration).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Final

from stalbot.domain.enums import ItemCategory
from stalbot.domain.money import Rub

#: Canonical section order (заявка 21.08.2026 п.2) — matches the boost
#: poster's own section order (`layout_boosts.json`, Часть IX) so the
#: order-boosts picker groups items the same way the posters do. A section
#: not in this tuple (or `None`) sorts after every known one.
CATALOG_SECTION_ORDER: Final[tuple[str, ...]] = (
    "Кулинария",
    "Самогоноварение",
    "Медицина",
    "Пиротехника",
    "Боеприпасы",
    "Прочее",
)


@dataclass(frozen=True, slots=True)
class CatalogItem:
    """A resource the bot buys, or a boost the bot sells — never both (§I.5)."""

    id: int | None
    """`None` for a not-yet-persisted item — the repository assigns one on insert."""
    name: str
    name_norm: str
    category: ItemCategory
    section: str | None
    """Кулинария / Самогоноварение / Медицина / Пиротехника / Боеприпасы /
    Прочее — matches the shelter professions (§I.7). `None` where the
    import couldn't determine one (see Э4's import report)."""
    price_buy: Rub | None
    price_sell: Rub | None
    emoji: str | None
    sort_order: int
    shelter_item_id: int | None
    """Bridge to `shelter_items` (Э5). `None` = not yet mapped/confirmed."""
    created_at: datetime
    updated_at: datetime | None
    deleted_at: datetime | None
    """Soft-delete marker — a deleted item's id is never reused/renumbered."""
