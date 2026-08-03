"""Boost-order draft management: the in-place editor's backing state (PLAN.md §11.6).

`BoostOrderService` never touches Discord or Sheets — it only reconciles
`boost_order_lines` against the live item catalog. Prices are always read
fresh from `ItemsCacheRepository` rather than cached on the line itself, so
a `/setboost` price change between "added to the draft" and "confirmed"
is never stale (PLAN.md §11.6: "цена берётся... на момент подтверждения").
"""

from collections.abc import Sequence
from dataclasses import replace
from decimal import Decimal

from stalbot.application.dto.boost_order_line import BoostOrderLine
from stalbot.domain.entities.item import Item
from stalbot.domain.enums import ItemCategory
from stalbot.infrastructure.cache.repositories.boost_order_lines import BoostOrderLinesRepository
from stalbot.infrastructure.cache.repositories.items import (
    ItemsCacheRepository,
    normalize_item_name,
)

#: PLAN.md §11.6: quantity modal validates `1 ≤ qty ≤ 9999`.
MIN_QUANTITY = 1
MAX_QUANTITY = 9999

_DEFAULT_QUANTITY = 1


class BoostOrderService:
    """Backs the boost-order editor: draft lines, quantities, live pricing."""

    def __init__(self, lines: BoostOrderLinesRepository, items: ItemsCacheRepository) -> None:
        """Wire the service to its collaborators.

        Args:
            lines: Cache repository for `boost_order_lines`.
            items: Cache repository for the item catalog (live prices, the
                full boost list for the "add boosts" picker).
        """
        self._lines = lines
        self._items = items

    async def list_available_boosts(self) -> Sequence[Item]:
        """Return every catalog item in the `BOOST` category, for the "add boosts" picker."""
        return await self._items.by_category(ItemCategory.BOOST)

    async def list_lines(self, channel_id: int) -> Sequence[BoostOrderLine]:
        """Return every draft line for a ticket channel.

        Args:
            channel_id: Discord channel id of the order-boosts ticket.
        """
        return await self._lines.list_for_channel(channel_id)

    async def list_lines_with_items(
        self, channel_id: int
    ) -> list[tuple[BoostOrderLine, Item | None]]:
        """Return every draft line paired with its current catalog item.

        `Item` is `None` if the item was deleted from the catalog since
        being added to the draft — the caller decides how to render that
        (PLAN.md §11.6 doesn't specify, so the card simply omits the line
        rather than crashing; `/del_item` already prunes the line itself,
        `application/services/catalog.py::CatalogService.delete_item`, so
        this is a narrow race-window concern, not the steady state).

        Args:
            channel_id: Discord channel id of the order-boosts ticket.
        """
        lines = await self._lines.list_for_channel(channel_id)
        result: list[tuple[BoostOrderLine, Item | None]] = []
        for line in lines:
            item = await self._items.get_by_id(line.item_id)
            result.append((line, item))
        return result

    async def apply_page_selection(
        self, channel_id: int, page_items: Sequence[Item], selected_ids: frozenset[int]
    ) -> None:
        """Reconcile one multiselect page against the draft (PLAN.md §11.6).

        Only items shown on *this* page are touched — a line for an item on
        a different page is left alone, which is what makes cross-page
        selection state work without holding it in the View.

        Args:
            channel_id: The order-boosts ticket channel.
            page_items: The catalog items that were shown as options on the
                page the player just interacted with.
            selected_ids: The item ids checked in that interaction.
        """
        existing_ids = {line.item_id for line in await self._lines.list_for_channel(channel_id)}
        for item in page_items:
            if item.id in selected_ids and item.id not in existing_ids:
                await self._lines.upsert(
                    BoostOrderLine(
                        channel_id=channel_id,
                        item_id=item.id,
                        item_name_norm=normalize_item_name(item.name),
                        category=item.category,
                        quantity=_DEFAULT_QUANTITY,
                    )
                )
            elif item.id not in selected_ids and item.id in existing_ids:
                await self._lines.delete_line(channel_id, item.id)

    async def set_quantity(self, channel_id: int, item_id: int, quantity: int) -> None:
        """Set a line's quantity outright (the `🔢 Ввести количество` modal).

        Args:
            channel_id: The order-boosts ticket channel.
            item_id: The line's catalog item id.
            quantity: New quantity, already validated by the caller
                (`MIN_QUANTITY..MAX_QUANTITY`).
        """
        line = await self._find_line(channel_id, item_id)
        if line is None:
            return
        await self._lines.upsert(replace(line, quantity=quantity))

    async def adjust_quantity(self, channel_id: int, item_id: int, delta: int) -> int | None:
        """Adjust a line's quantity by `delta`, clamped to the valid range.

        Args:
            channel_id: The order-boosts ticket channel.
            item_id: The line's catalog item id.
            delta: `+1` (increment) or `-1` (decrement).

        Returns:
            The line's new quantity, or `None` if it no longer exists.
        """
        line = await self._find_line(channel_id, item_id)
        if line is None:
            return None
        quantity = max(MIN_QUANTITY, min(MAX_QUANTITY, line.quantity + delta))
        await self._lines.upsert(replace(line, quantity=quantity))
        return quantity

    async def remove_line(self, channel_id: int, item_id: int) -> None:
        """Remove one line from the draft (`🗑️ Удалить`).

        Args:
            channel_id: The order-boosts ticket channel.
            item_id: The line's catalog item id.
        """
        await self._lines.delete_line(channel_id, item_id)

    async def compute_total(self, channel_id: int) -> Decimal:
        """Sum every line's `quantity * price_sell`, read fresh from the catalog.

        Args:
            channel_id: The order-boosts ticket channel.
        """
        total = Decimal(0)
        for line, item in await self.list_lines_with_items(channel_id):
            if item is not None and item.price_sell is not None:
                total += item.price_sell * line.quantity
        return total

    async def clear(self, channel_id: int) -> None:
        """Drop every draft line once the order is confirmed.

        Args:
            channel_id: The order-boosts ticket channel.
        """
        await self._lines.clear_channel(channel_id)

    async def _find_line(self, channel_id: int, item_id: int) -> BoostOrderLine | None:
        lines = await self._lines.list_for_channel(channel_id)
        return next((line for line in lines if line.item_id == item_id), None)
