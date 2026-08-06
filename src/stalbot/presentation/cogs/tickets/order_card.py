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

_EDITOR_TITLE = "🧾 Редактор заказа"
_SUMMARY_TITLE = "🧾 Заказ бустов"
_SEPARATOR = "━━━━━━━━━━━━━━━━━━━━━"


def _order_body(
    session: TicketSession, lines_with_items: Sequence[tuple[BoostOrderLine, Item | None]]
) -> list[str]:
    """The order's line list + total + deadline — shared by the editor and summary embeds.

    Args:
        session: The ticket's persisted state (for the deadline).
        lines_with_items: Draft lines paired with their current catalog
            item (`None` if the item was deleted since being added — such
            a line is silently omitted from the total and the list).
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
    return body


def render_order_editor(
    session: TicketSession,
    lines_with_items: Sequence[tuple[BoostOrderLine, Item | None]],
    embeds: EmbedFactory,
) -> discord.Embed:
    """Build the boost-order editor embed from `session` and its draft lines (UX #1).

    Returns:
        The editor card embed, with the interactive line-picker/quantity
        controls (`OrderEditorView`) — reached via the summary embed's
        "✏️ Редактировать" button.
    """
    body = _order_body(session, lines_with_items)
    return embeds.ticket(session.kind, "\n".join(body), title=_EDITOR_TITLE)


def render_order_summary(
    session: TicketSession,
    lines_with_items: Sequence[tuple[BoostOrderLine, Item | None]],
    embeds: EmbedFactory,
) -> discord.Embed:
    """Build the read-only boost-order summary embed (UX #1).

    Same body as `render_order_editor` (same draft, same total) but paired
    with `OrderSummaryView` instead — "✏️ Редактировать" (any participant)
    to reopen the editor, "🏁 Завершить заказ" (admin-only) to register the
    deal. Shown first after the order form is submitted, and again after
    the editor's "✅ Подтвердить".
    """
    body = _order_body(session, lines_with_items)
    return embeds.ticket(session.kind, "\n".join(body), title=_SUMMARY_TITLE)
