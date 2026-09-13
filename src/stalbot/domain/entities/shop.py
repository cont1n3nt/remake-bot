"""Stored shop rows: categories, items, purchases and the effects they attach.

заявка 13.09.2026 п.2. Four small entities rather than one, because they
have genuinely different lifetimes: a category is a heading the owner
renames, an item is an offer they edit, a purchase is a financial fact that
never changes once written, and an effect is live state that expires and
gets consumed.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class ShopCategory:
    """One tier of the assortment («Мелкие товары», «Элитные привилегии», …)."""

    key: str
    name: str
    description: str | None = None
    sort_order: int = 0
    active: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ShopItem:
    """One thing on sale."""

    id: int | None
    category_key: str
    name: str
    name_norm: str
    description: str
    price_coins: int
    effect_kind: str
    """What buying it does. Free text — a new kind of perk must not need a
    migration. See `domain/shop/effects.py` for the ones the bot applies
    by itself."""
    effect_value: str | None = None
    """Read according to `effect_kind`: a percent, an amount, a range, or JSON."""
    duration_days: int | None = None
    """`None` = the effect has no deadline of its own."""
    uses: int | None = None
    """`None` = not counted by uses. 1 = разовый, 3 = «на 3 сделки»."""
    stock: int | None = None
    """`None` = unlimited."""
    per_player_limit: int | None = None
    """`None` = a player may buy it any number of times."""
    emoji: str | None = None
    sort_order: int = 0
    active: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None
    deleted_at: datetime | None = None

    @property
    def is_available(self) -> bool:
        """Whether the item should appear on the shelf at all."""
        return self.active and self.deleted_at is None and (self.stock is None or self.stock > 0)


@dataclass(frozen=True, slots=True)
class ShopPurchase:
    """One completed purchase. Never edited — only its `status` moves."""

    id: int | None
    player_id: int
    shop_item_id: int
    price_coins: int
    """Snapshot of the price paid. A later price change must not alter what
    a refund pays back."""
    status: str = "active"
    """`active` | `used` | `expired` | `refunded`."""
    purchased_at: datetime | None = None
    refunded_at: datetime | None = None
    refunded_by: int | None = None
    idempotency_key: str | None = None
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PlayerEffect:
    """What a purchase attached to a player — the live half of it."""

    id: int | None
    player_id: int
    effect_kind: str
    purchase_id: int | None = None
    effect_value: str | None = None
    uses_left: int | None = None
    """`None` = not counted by uses."""
    expires_at: datetime | None = None
    """`None` = no deadline."""
    note: str | None = None
    created_at: datetime | None = None
    consumed_at: datetime | None = None
    """Set once the effect is spent, expired or refunded away. A consumed
    effect is kept, not deleted — it is the record of what a purchase did."""

    def is_live_at(self, now: datetime) -> bool:
        """Whether this effect still applies at *now*.

        Args:
            now: The moment being asked about, tz-aware.
        """
        if self.consumed_at is not None:
            return False
        if self.expires_at is not None and self.expires_at <= now:
            return False
        return self.uses_left is None or self.uses_left > 0
