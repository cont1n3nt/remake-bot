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


class TicketStatus(StrEnum):
    """A ticket session's place in the flow (PLAN.md §11.2–§11.5)."""

    AWAITING_TOOL = "awaiting_tool"
    """Channel just created; waiting for Ticket Tool's own first message."""

    AWAITING_FORM = "awaiting_form"
    """Panel posted, delivery method picked; the player's form modal is next."""

    FILLED = "filled"
    """Form submitted; the summary card is up, waiting for admin confirmation."""

    CONFIRMED = "confirmed"
    """Admin confirmed the deal; `TransactionService.register()` has run."""


class DeliveryMethod(StrEnum):
    """How a player will send in what they're selling (PLAN.md §11.3)."""

    MAIL = "mail"
    """📬 Почта — sent to the player's in-game mailbox."""

    TRADE = "trade"
    """🤝 Обмен — a direct in-game trade."""
