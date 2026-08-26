"""`TempPrice` — a row of the `temp_prices` table (заявка 21.08.2026 п.9).

migration `0008_temp_prices.sql`. Only remembers what to revert *to* and
*when* — the live override is a plain `catalog_items.price_buy`/`price_sell`
write, same column `/setprice` writes.
"""

from dataclasses import dataclass
from datetime import datetime

from stalbot.domain.enums import PriceField
from stalbot.domain.money import Rub


@dataclass(frozen=True, slots=True)
class TempPrice:
    """One active (not yet expired/reverted) temporary price override."""

    id: int | None
    """`None` for a not-yet-persisted row — the repository assigns one on insert."""
    item_id: int
    field: PriceField
    original_price: Rub | None
    """The price to revert to — `None` if the item had no price on this field before."""
    expires_at: datetime
    created_by: int | None
    created_at: datetime
