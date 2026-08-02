"""Small, stable enums shared across the domain (see PLAN.md §4)."""

from enum import StrEnum


class DealType(StrEnum):
    """Which side of a `/add` transaction was recorded."""

    PURCHASE = "purchase"
    """У меня — the bot bought from the player."""

    SALE = "sale"
    """Мне — the bot sold to the player."""


class ItemCategory(StrEnum):
    """Catalog item category."""

    RESOURCE = "resource"
    BOOST = "boost"


class TicketKind(StrEnum):
    """Which of the three tracked ticket categories a channel belongs to."""

    SELL_ITEMS = "sell_items"
    SELL_BOOSTS = "sell_boosts"
    ORDER_BOOSTS = "order_boosts"


class PriceField(StrEnum):
    """Which `Item` price a `SheetLayout` block feeds (see PLAN.md §6.2)."""

    BUY = "buy"
    SELL = "sell"
