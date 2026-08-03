"""`Item` — a row of the `item database` block (PLAN.md §6.1, §6.4)."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from stalbot.domain.enums import ItemCategory


@dataclass(frozen=True, slots=True)
class Item:
    """A catalog entry: a resource or a boost, with buy/sell prices."""

    id: int
    name: str
    category: ItemCategory
    price_buy: Decimal | None
    price_sell: Decimal | None
    emoji: str | None
    updated_at: datetime | None
    row: int
