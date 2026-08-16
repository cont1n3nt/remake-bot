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


class PosterKind(StrEnum):
    """Which of the three `/poster` layouts to render (Часть IX, Э11).

    Matches `scripts/extract_poster_assets.py`'s `_KIND_BY_SHEET` values —
    the layout JSON filenames are `layout_<value>.json`. `RESOURCES` and
    `BOOST_PURCHASES` both price against `ItemCategory.RESOURCE` (the bot
    buying side, §I.5) — `BOOST_PURCHASES` is just a curated subset of that
    same category (finished boost items bought back from players).
    """

    RESOURCES = "resources"
    BOOSTS = "boosts"
    BOOST_PURCHASES = "boost_purchases"


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


class OccurredAtKind(StrEnum):
    """How trustworthy a `Deal.occurred_at` timestamp is (sqlite_migration.md §I.3, §IV.1)."""

    UNKNOWN = "unknown"
    """Default; should not occur for a deal actually written by this schema."""

    SHEET_TEXT = "sheet_text"
    """Imported from a sheet cell that held real text like `"27.07.26 21:31"`."""

    SHEET_DATE = "sheet_date"
    """Imported from a sheet cell Google Sheets stored as its own date type."""

    SHEET_INTERPOLATED = "sheet_interpolated"
    """The 534 dateless historical deals (§I.3) — evenly interpolated, not
    recorded. The presentation layer must mark these as "date approximate"."""

    BOT = "bot"
    """Recorded live by the bot itself (`/add`, ticket confirmation) — exact."""


class DealSource(StrEnum):
    """What produced a `Deal` row (sqlite_migration.md §IV.1)."""

    ADD = "add"
    """The `/add` command."""

    TICKET = "ticket"
    """A confirmed sell/buy ticket."""

    IMPORT = "import"
    """`scripts/import_from_sheets.py` (Э4)."""


class PriceChangeSource(StrEnum):
    """What produced an `item_price_history` row (sqlite_migration.md §IV.2, Э7)."""

    SETPRICE = "setprice"
    """`/setprice` or `/setboost`."""

    IMPORT = "import"
    """`/new_price`'s TXT import."""

    CATALOG = "catalog"
    """A price set at `/item_add` time."""

    MIGRATION = "migration"
    """A one-time importer (Э4's `scripts/import_from_sheets.py`)."""
