"""Builds the скупка calculator's embed from state (sqlite_migration.md §I.9, Э8).

Same invariant as `tickets/order_card.py`: always built fresh from
`boost_order_lines` + the live catalog, never assembled ad hoc in a handler.
"""

from collections.abc import Sequence
from decimal import Decimal

from stalbot.application.dto.boost_order_line import BoostOrderLine
from stalbot.domain.entities.catalog_item import CatalogItem
from stalbot.domain.enums import ItemCategory
from stalbot.domain.money import format_amount

_SEPARATOR = "━━━━━━━━━━━━━━━━━━━━━"


def render_calculator_body(
    lines_with_items: Sequence[tuple[BoostOrderLine, CatalogItem | None]], total: Decimal
) -> str:
    """Render the line list + position count + total for the calculator card.

    `EmbedFactory` truncates the description to Discord's 4096-char cap on
    its own (§I.9: up to ~160 positions can, in the worst case, run past
    that) — a hard cut with an ellipsis rather than pagination, an
    intentional first cut (sqlite_migration.md Э8, часть 2).

    Args:
        lines_with_items: Draft lines paired with their current catalog
            item (`None` if the item was deleted from the catalog since
            being added — such a line is omitted, same as the boost-order
            editor).
        total: Precomputed via `BoostOrderService.compute_total`.
    """
    body = [_SEPARATOR]
    live_lines = [(line, item) for line, item in lines_with_items if item is not None]
    if live_lines:
        for line, item in live_lines:
            price = item.price_buy if item.category is ItemCategory.RESOURCE else item.price_sell
            subtotal = (price or Decimal(0)) * line.quantity
            body.append(f" {item.name} × {line.quantity} = {format_amount(subtotal)}")
    else:
        body.append("Список пока пуст — нажмите «📋 Вставить список».")
    body.append("")
    body.append(_SEPARATOR)
    body.append(f"📦 Позиций: {len(live_lines)}")
    body.append(f"💰 Итого: {format_amount(total)}")
    return "\n".join(body)
