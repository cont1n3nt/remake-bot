"""Builds the one ticket summary card embed from `TicketSession` state (PLAN.md §11.5).

The project invariant this module exists to satisfy: the card is *always*
built by `render_ticket_card(session)` from persisted state, never
assembled ad hoc in a handler — so adding the OCR block in M13 means
editing this one function, not every place a card gets sent or edited.
Empty fields (no referrer yet, no screenshot yet) are simply omitted.
"""

import discord

from stalbot.application.dto.ticket_session import TicketSession
from stalbot.domain.clock import format_datetime
from stalbot.domain.enums import DeliveryMethod
from stalbot.presentation.embeds.factory import EmbedFactory

#: Filename every screenshot is re-uploaded under (PLAN.md §11.5) — the
#: card's embed always points at `attachment://SCREENSHOT_FILENAME`, which
#: keeps resolving across later edits as long as that attachment stays on
#: the message (Discord does not require the file to be re-sent on an edit
#: that otherwise leaves `attachments` untouched).
SCREENSHOT_FILENAME = "screenshot.png"

_SEPARATOR = "━━━━━━━━━━━━━━━━━━━━━"

_DELIVERY_LABELS: dict[DeliveryMethod, str] = {
    DeliveryMethod.MAIL: "📬 Почта",
    DeliveryMethod.TRADE: "🤝 Обмен",
}


def render_ticket_card(session: TicketSession, embeds: EmbedFactory) -> discord.Embed:
    """Build the public ticket summary card from `session`'s current state.

    Args:
        session: The ticket's persisted state.
        embeds: Factory used to build the underlying `discord.Embed`.

    Returns:
        The card embed. If `session.screenshot_message_id` is set, the
        embed's image points at `attachment://screenshot.png` — the caller
        is responsible for that attachment actually being present on the
        message it sends/edits this embed onto.
    """
    lines = [_SEPARATOR, f"👤 Игрок <@{session.author_id}>"]
    if session.game_nick:
        lines.append(f"🎮 Игровой ник {session.game_nick}")
    if session.delivery_method is not None:
        lines.append(f"📮 Способ {_DELIVERY_LABELS[session.delivery_method]}")
    if session.referrer_nick:
        referrer = session.referrer_nick
        if session.referrer_discord_id is not None:
            referrer += f" (<@{session.referrer_discord_id}>)"
        lines.append(f"🤝 Пригласил {referrer}")
    lines.append(f"🕒 Создана {format_datetime(session.created_at)}")

    embed = embeds.ticket(session.kind, "\n".join(lines))
    if session.screenshot_message_id is not None:
        embed.set_image(url=f"attachment://{SCREENSHOT_FILENAME}")
    return embed
