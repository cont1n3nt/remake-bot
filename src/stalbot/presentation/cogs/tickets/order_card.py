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
from stalbot.domain.entities.catalog_item import CatalogItem
from stalbot.domain.money import format_amount
from stalbot.domain.progression.ranks import RankTier
from stalbot.presentation.cogs.tickets.card import (
    DELIVERY_LABELS,
    format_coupon_line,
    format_role_markup,
)
from stalbot.presentation.embeds.factory import EmbedFactory

_EDITOR_TITLE = "🧾 Редактор заказа"
_SUMMARY_TITLE = "🧾 Заказ бустов"
_SEPARATOR = "━━━━━━━━━━━━━━━━━━━━━"
_NO_MARKUP = Decimal("1.00")


def _order_body(
    session: TicketSession,
    lines_with_items: Sequence[tuple[BoostOrderLine, CatalogItem | None]],
    *,
    rank_tier: RankTier | None,
    price_multiplier: Decimal,
) -> list[str]:
    """The order's line list + total + deadline — shared by the editor and summary embeds.

    Args:
        session: The ticket's persisted state (for the deadline).
        lines_with_items: Draft lines paired with their current catalog
            item (`None` if the item was deleted since being added — such
            a line is silently omitted from the total and the list).
        rank_tier: The author's current rank tier, for the markup/discount
            note — `None` if they hold no rank role.
        price_multiplier: Applied to the raw total (§9.1, заявка
            21.08.2026 п.2). `1.00` is shown as-is, with no extra note.
    """
    body = [_SEPARATOR, f"👤 Игрок: <@{session.author_id}>"]
    if session.game_nick:
        body.append(f"🎮 Игровой ник: {session.game_nick}")
    if session.delivery_method is not None:
        body.append(f"📮 Способ: {DELIVERY_LABELS[session.delivery_method]}")
    body.append("")
    body.append(_SEPARATOR)

    raw_total = Decimal(0)
    live_lines = [(line, item) for line, item in lines_with_items if item is not None]
    if live_lines:
        body.append("Ваш заказ:")
        for line, item in live_lines:
            price = item.price_sell or Decimal(0)
            subtotal = price * line.quantity
            raw_total += subtotal
            body.append(f" {item.name} × {line.quantity} = {format_amount(subtotal)}")
    else:
        body.append("Заказ пока пуст — нажмите «➕ Добавить бусты».")
    body.append("")
    body.append(_SEPARATOR)

    coupon_multiplier = _NO_MARKUP
    if session.coupon_discount_percent is not None:
        coupon_multiplier = (Decimal(100) - session.coupon_discount_percent) / Decimal(100)

    total = raw_total * price_multiplier * coupon_multiplier
    show_breakdown = (
        price_multiplier != _NO_MARKUP and rank_tier is not None
    ) or coupon_multiplier != _NO_MARKUP
    if show_breakdown:
        body.append(f"💰 Сумма: {format_amount(raw_total)}")
        if price_multiplier != _NO_MARKUP and rank_tier is not None:
            body.append(format_role_markup(rank_tier, price_multiplier))
        if coupon_multiplier != _NO_MARKUP:
            assert session.coupon_code is not None  # noqa: S101 - set together
            assert session.coupon_kind is not None  # noqa: S101 - set together
            assert session.coupon_discount_percent is not None  # noqa: S101 - set together
            body.append(
                format_coupon_line(
                    session.coupon_code,
                    session.coupon_kind,
                    session.coupon_discount_percent,
                    verb="Промокод",
                )
            )
    body.append(f"💰 Итого: {format_amount(total)}")
    if session.deadline is not None:
        body.append(f"⏳ Срок: {format_datetime(session.deadline)}")
    return body


def render_order_editor(
    session: TicketSession,
    lines_with_items: Sequence[tuple[BoostOrderLine, CatalogItem | None]],
    embeds: EmbedFactory,
    *,
    rank_tier: RankTier | None = None,
    price_multiplier: Decimal = _NO_MARKUP,
) -> discord.Embed:
    """Build the boost-order editor embed from `session` and its draft lines (UX #1).

    Returns:
        The editor card embed, with the interactive line-picker/quantity
        controls (`OrderEditorView`) — reached via the summary embed's
        "✏️ Редактировать" button.
    """
    body = _order_body(
        session, lines_with_items, rank_tier=rank_tier, price_multiplier=price_multiplier
    )
    return embeds.ticket(session.kind, "\n".join(body), title=_EDITOR_TITLE)


def render_order_summary(
    session: TicketSession,
    lines_with_items: Sequence[tuple[BoostOrderLine, CatalogItem | None]],
    embeds: EmbedFactory,
    *,
    rank_tier: RankTier | None = None,
    price_multiplier: Decimal = _NO_MARKUP,
) -> discord.Embed:
    """Build the read-only boost-order summary embed (UX #1).

    Same body as `render_order_editor` (same draft, same total) but paired
    with `OrderSummaryView` instead — "✏️ Редактировать" (any participant)
    to reopen the editor, "🏁 Завершить заказ" (admin-only) to register the
    deal. Shown first after the order form is submitted, and again after
    the editor's "✅ Подтвердить".
    """
    body = _order_body(
        session, lines_with_items, rank_tier=rank_tier, price_multiplier=price_multiplier
    )
    return embeds.ticket(session.kind, "\n".join(body), title=_SUMMARY_TITLE)
