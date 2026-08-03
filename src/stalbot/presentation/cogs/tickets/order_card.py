"""Builds the boost-order editor embed from state (PLAN.md §11.6).

Same invariant as `card.py`'s `render_ticket_card`: the editor's embed is
always built by `render_order_editor(...)` from persisted state
(`boost_order_lines` + the live catalog), never assembled ad hoc in a
handler — every mutation just re-renders from scratch.
"""

from collections.abc import Sequence
from decimal import Decimal

import discord

from stalbot.application.dto.boost_order_line import BoostOrderLine
from stalbot.application.dto.ticket_session import TicketSession
from stalbot.domain.clock import format_datetime
from stalbot.domain.entities.item import Item
from stalbot.domain.money import format_amount
from stalbot.presentation.embeds.factory import EmbedFactory

_TITLE = "🧾 Редактор заказа"
_SEPARATOR = "━━━━━━━━━━━━━━━━━━━━━"


def render_order_editor(
    session: TicketSession,
    lines_with_items: Sequence[tuple[BoostOrderLine, Item | None]],
    embeds: EmbedFactory,
) -> discord.Embed:
    """Build the boost-order editor embed from `session` and its draft lines.

    Args:
        session: The ticket's persisted state (for the deadline).
        lines_with_items: Draft lines paired with their current catalog
            item (`None` if the item was deleted since being added — such
            a line is silently omitted from the total and the list).
        embeds: Factory used to build the underlying `discord.Embed`.

    Returns:
        The editor card embed.
    """
    body = [_SEPARATOR]
    total = Decimal(0)
    live_lines = [(line, item) for line, item in lines_with_items if item is not None]
    if live_lines:
        body.append("Ваш заказ:")
        for line, item in live_lines:
            price = item.price_sell or Decimal(0)
            subtotal = price * line.quantity
            total += subtotal
            body.append(f" {item.name} × {line.quantity} = {format_amount(subtotal)}")
    else:
        body.append("Заказ пока пуст — нажмите «➕ Добавить бусты».")
    body.append(_SEPARATOR)
    body.append(f"💰 Итого: {format_amount(total)}")
    if session.deadline is not None:
        body.append(f"⏳ Срок: {format_datetime(session.deadline)}")

    return embeds.ticket(session.kind, "\n".join(body), title=_TITLE)
