"""Boost-order draft management: the in-place editor's backing state (PLAN.md §11.6).

`BoostOrderService` never touches Discord or Sheets — it only reconciles
`boost_order_lines` against the live item catalog. Prices are always read
fresh from `CatalogItemsRepository` rather than cached on the line itself, so
a `/setboost` price change between "added to the draft" and "confirmed"
is never stale (PLAN.md §11.6: "цена берётся... на момент подтверждения").

sqlite_migration.md Э8: the same `boost_order_lines`-backed draft also
serves the daily скупка calculator (§I.9), which admits resources as well
as boosts and — via `apply_bulk_entry` — can grow past `MAX_ORDER_LINES`.
That cap stays scoped to `apply_page_selection`'s Select-based picker (the
only path actually bounded by Discord's 25-option limit).
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Final

from stalbot.application.dto.boost_order_line import BoostOrderLine
from stalbot.application.dto.bulk_entry_report import BulkEntryReport
from stalbot.domain.entities.catalog_item import CatalogItem
from stalbot.domain.enums import ItemCategory
from stalbot.infrastructure.cache.repositories.boost_order_lines import BoostOrderLinesRepository
from stalbot.infrastructure.cache.repositories.catalog_items import CatalogItemsRepository
from stalbot.infrastructure.cache.repositories.items import normalize_item_name

#: PLAN.md §11.6: quantity modal validates `1 ≤ qty ≤ 9999`.
MIN_QUANTITY = 1
MAX_QUANTITY = 9999

#: Discord's `Select` component hard-caps at 25 options, and the *ticket*
#: order editor renders one per draft line in a single `Select` — so a draft
#: reachable through that picker must never exceed this many lines (TICK-5).
#: Nothing upstream enforces it: the "add boosts" picker paginates the
#: *catalog* 25/page, not the cumulative draft, so a player can otherwise
#: check boosts across enough pages to push the draft past the limit and
#: crash the editor's every future render. `apply_bulk_entry` (Э8) does NOT
#: check this cap — the скупка calculator has no Select to overflow and is
#: explicitly meant to hold 100+ lines (sqlite_migration.md §I.9).
MAX_ORDER_LINES: Final = 25

_DEFAULT_QUANTITY = 1

#: "Название xКоличество" / "Название х5" / "Название×5" — Latin/Cyrillic
#: х and × all accepted, since the owner types this by hand (§I.9, Э8).
_BULK_LINE_RE = re.compile(r"^(?P<name>.+?)\s*[xхX×]\s*(?P<quantity>\d+)\s*$")


@dataclass(frozen=True, slots=True)
class _ParsedBulkLine:
    raw: str
    name: str
    quantity: int


def _parse_bulk_entry_lines(text: str) -> tuple[list[_ParsedBulkLine], list[str]]:
    """Split a bulk paste into successfully-parsed lines and raw rejects.

    `xКоличество` of `0` is a valid parse — `apply_bulk_entry` treats it as
    "remove this line" (Э8's point-edit path: there's no Select to pick a
    line out of a 100+-line draft, so a re-pasted "Название x0" is how a
    single line gets deleted instead).

    Pure and Discord-agnostic on purpose — every format edge case belongs
    in a unit test here, not exercised through a modal.
    """
    parsed: list[_ParsedBulkLine] = []
    not_parsed: list[str] = []
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        match = _BULK_LINE_RE.match(stripped)
        name = match.group("name").strip() if match else ""
        quantity = int(match.group("quantity")) if match else -1
        if match is None or not name or not (0 <= quantity <= MAX_QUANTITY):
            not_parsed.append(raw_line)
            continue
        parsed.append(_ParsedBulkLine(raw=raw_line, name=name, quantity=quantity))
    return parsed, not_parsed


class BoostOrderService:
    """Backs the boost-order editor: draft lines, quantities, live pricing."""

    def __init__(self, lines: BoostOrderLinesRepository, items: CatalogItemsRepository) -> None:
        """Wire the service to its collaborators.

        Args:
            lines: Cache repository for `boost_order_lines`.
            items: Cache repository for the item catalog (live prices, the
                full boost list for the "add boosts" picker).
        """
        self._lines = lines
        self._items = items

    async def list_available_items(self) -> Sequence[CatalogItem]:
        """Return every sellable catalog item, for the "add boosts" picker.

        Both categories — a boost order isn't boosts-only, the same picker
        is how resources get ordered through this ticket too. "Sellable"
        means `price_sell` is set; an item with no sell price can't be
        priced into an order line at all.
        """
        return [item for item in await self._items.all() if item.price_sell is not None]

    async def list_lines(self, channel_id: int) -> Sequence[BoostOrderLine]:
        """Return every draft line for a ticket channel.

        Args:
            channel_id: Discord channel id of the order-boosts ticket.
        """
        return await self._lines.list_for_channel(channel_id)

    async def list_lines_with_items(
        self, channel_id: int
    ) -> list[tuple[BoostOrderLine, CatalogItem | None]]:
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
        # One batched lookup instead of one `get_by_id` per line (APP-6).
        items = await self._items.get_by_ids([line.item_id for line in lines])
        return [(line, items.get(line.item_id)) for line in lines]

    async def apply_page_selection(
        self, channel_id: int, page_items: Sequence[CatalogItem], selected_ids: frozenset[int]
    ) -> frozenset[int]:
        """Reconcile one multiselect page against the draft (PLAN.md §11.6).

        Only items shown on *this* page are touched — a line for an item on
        a different page is left alone, which is what makes cross-page
        selection state work without holding it in the View.

        Args:
            channel_id: The order-boosts ticket channel.
            page_items: The catalog items that were shown as options on the
                page the player just interacted with.
            selected_ids: The item ids checked in that interaction.

        Returns:
            Ids from *selected_ids* that were newly checked but NOT added,
            because the draft was already at `MAX_ORDER_LINES` (TICK-5).
        """
        persisted_items = [item for item in page_items if item.id is not None]
        existing_ids = {line.item_id for line in await self._lines.list_for_channel(channel_id)}
        to_add = [
            item
            for item in persisted_items
            if item.id in selected_ids and item.id not in existing_ids
        ]
        to_remove = [
            item
            for item in persisted_items
            if item.id not in selected_ids and item.id in existing_ids
        ]
        # Removals first: a page can uncheck one item and check another in
        # the same submission, and the freed slot must count toward the cap
        # check below — doing this in `page_items`' own (catalog-id) order
        # instead would reject the addition whenever it happens to come
        # first, even though the net effect stays within `MAX_ORDER_LINES`.
        for item in to_remove:
            assert item.id is not None  # noqa: S101 - filtered into persisted_items above
            await self._lines.delete_line(channel_id, item.id)
            existing_ids.discard(item.id)

        rejected: set[int] = set()
        for item in to_add:
            assert item.id is not None  # noqa: S101 - filtered into persisted_items above
            if len(existing_ids) >= MAX_ORDER_LINES:
                rejected.add(item.id)
                continue
            await self._lines.upsert(
                BoostOrderLine(
                    channel_id=channel_id,
                    item_id=item.id,
                    item_name_norm=normalize_item_name(item.name),
                    category=item.category,
                    quantity=_DEFAULT_QUANTITY,
                )
            )
            existing_ids.add(item.id)
        return frozenset(rejected)

    async def set_quantity(self, channel_id: int, item_id: int, quantity: int) -> None:
        """Set a line's quantity outright (the `🔢 Ввести количество` modal).

        Args:
            channel_id: The order-boosts ticket channel.
            item_id: The line's catalog item id.
            quantity: New quantity. Clamped to `MIN_QUANTITY..MAX_QUANTITY`
                here too (APP-7), not just trusted from the caller (like
                `adjust_quantity` already does) — this method has no other
                guard of its own if a future caller skips validation.
        """
        line = await self._find_line(channel_id, item_id)
        if line is None:
            return
        clamped = max(MIN_QUANTITY, min(MAX_QUANTITY, quantity))
        await self._lines.upsert(replace(line, quantity=clamped))

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
        """Sum every line's `quantity * price`, read fresh from the catalog.

        A resource only ever has `price_buy`, a boost only ever `price_sell`
        (§I.5's `CHECK`) — so "the line's price" is unambiguous per
        category. This is the скупка calculator's total (Э8, "buying from
        the player" direction) — the boost-order ticket, which sells *to*
        the player, uses `compute_order_total` instead.

        Args:
            channel_id: The order-boosts ticket channel.
        """
        total = Decimal(0)
        for line, item in await self.list_lines_with_items(channel_id):
            if item is None:
                continue
            price = item.price_sell if item.category is ItemCategory.BOOST else item.price_buy
            if price is not None:
                total += price * line.quantity
        return total

    async def compute_order_total(self, channel_id: int) -> Decimal:
        """Sum every line's `quantity * price_sell`, read fresh from the catalog.

        Always `price_sell`, resources included — a boost-order ticket is
        the bot *selling to* the player (opposite direction from the
        скупка calculator's `compute_total`), the same pricing
        `order_card.py`'s own displayed total already uses. Kept as its own
        method rather than branching `compute_total` on the caller, since
        the two calculators price resources in opposite directions and a
        shared method could not serve both without a flag.

        Args:
            channel_id: The order-boosts ticket channel.
        """
        total = Decimal(0)
        for line, item in await self.list_lines_with_items(channel_id):
            if item is None:
                continue
            if item.price_sell is not None:
                total += item.price_sell * line.quantity
        return total

    async def apply_bulk_entry(self, channel_id: int, text: str) -> BulkEntryReport:
        """Parse a "Название xКоличество"-per-line paste and upsert every resolved line.

        sqlite_migration.md §I.9, Э8: the owner's daily bulk-count workflow —
        deliberately does NOT enforce `MAX_ORDER_LINES` (that cap exists only
        for the ticket's Select-based picker, which this bypasses entirely).
        A line that fails to parse, matches no catalog item, or matches an
        item that exists in both categories (§I.5) is reported back rather
        than silently dropped, so the caller can show it for correction
        without discarding everything else that *did* resolve.

        `xКоличество` of `0` removes the line instead of upserting it — the
        calculator's point-edit path, since a 100+-line draft has no Select
        to pick a single line out of (unlike the ticket editor's `🗑️ Удалить`).

        Args:
            channel_id: The channel/ticket whose draft to update.
            text: Raw pasted text, one "Название xКоличество" entry per line.
        """
        parsed, not_parsed = _parse_bulk_entry_lines(text)
        catalog = await self._items.all()
        by_name: dict[str, list[CatalogItem]] = {}
        for item in catalog:
            by_name.setdefault(item.name_norm, []).append(item)

        applied: list[tuple[CatalogItem, int]] = []
        removed: list[CatalogItem] = []
        not_found: list[str] = []
        ambiguous: list[str] = []
        for line in parsed:
            matches = by_name.get(normalize_item_name(line.name), [])
            if not matches:
                not_found.append(line.raw)
                continue
            if len(matches) > 1:
                ambiguous.append(line.raw)
                continue
            item = matches[0]
            assert item.id is not None  # noqa: S101 - a cataloged item always has an id
            if line.quantity == 0:
                await self._lines.delete_line(channel_id, item.id)
                removed.append(item)
                continue
            await self._lines.upsert(
                BoostOrderLine(
                    channel_id=channel_id,
                    item_id=item.id,
                    item_name_norm=item.name_norm,
                    category=item.category,
                    quantity=line.quantity,
                )
            )
            applied.append((item, line.quantity))
        return BulkEntryReport(
            applied=applied,
            removed=removed,
            not_parsed=not_parsed,
            not_found=not_found,
            ambiguous=ambiguous,
        )

    async def clear(self, channel_id: int) -> None:
        """Drop every draft line once the order is confirmed.

        Args:
            channel_id: The order-boosts ticket channel.
        """
        await self._lines.clear_channel(channel_id)

    async def _find_line(self, channel_id: int, item_id: int) -> BoostOrderLine | None:
        lines = await self._lines.list_for_channel(channel_id)
        return next((line for line in lines if line.item_id == item_id), None)
