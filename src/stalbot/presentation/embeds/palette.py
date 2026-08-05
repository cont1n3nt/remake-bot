"""Visual constants shared by every embed: colors, footer, emoji dictionary.

See PLAN.md §5.3. Nothing here talks to the Discord API; it only supplies
values that `EmbedFactory` assembles into `discord.Embed` objects.
"""

from datetime import datetime
from typing import Final

from stalbot.domain.clock import format_datetime
from stalbot.domain.enums import TicketKind

#: Name shown in the footer of every embed, prefixed before its kind.
PLATFORM_NAME: Final = "Клондайк Шёпота"


class Color:
    """Embed accent colors, one per `EmbedFactory` builder (PLAN.md §5.3)."""

    SUCCESS: Final = 0x43B581
    INFO: Final = 0x5865F2
    WARNING: Final = 0xFAA61A
    ERROR: Final = 0xED4245
    TICKET: Final = 0x2B2D31
    AUDIT: Final = 0x9B59B6


class Emoji:
    """The one emoji dictionary used across every embed and command.

    Keeping every icon here guarantees the same concept (e.g. an audit
    trace id) renders with the same glyph everywhere it appears.
    """

    SUCCESS: Final = "✅"
    INFO: Final = "ℹ️"
    WARNING: Final = "⚠️"
    ERROR: Final = "❌"
    TICKET: Final = "🎫"
    AUDIT: Final = "🧾"
    USER: Final = "👤"
    CHANNEL: Final = "📍"
    COMMAND: Final = "⌨️"
    ARGUMENTS: Final = "📝"
    DURATION: Final = "⏱️"
    TRACE: Final = "🔗"


#: Fixed title text per ticket category (PLAN.md §11.2).
TICKET_TITLES: Final[dict[TicketKind, str]] = {
    TicketKind.SELL_ITEMS: f"{Emoji.TICKET} Заявка на продажу предметов",
    TicketKind.SELL_BOOSTS: f"{Emoji.TICKET} Заявка на продажу бустов",
    TicketKind.ORDER_BOOSTS: f"{Emoji.TICKET} Заявка на заказ бустов",
}


def build_footer(now: datetime, kind: str | None = None) -> str:
    """Render the shared footer.

    Without a *kind* (most embeds): `Клондайк Шёпота • 31.07.2026 21:45 (GMT+3)`.
    With one (e.g. log/ticket embeds): `Клондайк Шёпота | Логи • 31.07.2026 21:45 (GMT+3)`.
    """
    prefix = f"{PLATFORM_NAME} | {kind}" if kind else PLATFORM_NAME
    return f"{prefix} • {format_datetime(now)} (GMT+3)"
