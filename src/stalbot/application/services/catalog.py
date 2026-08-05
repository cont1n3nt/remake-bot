"""Item database CRUD (PLAN.md §10.9, §7.5).

`/item_add` and `/del_item` are the only two write paths onto the `item
database` block (`AA:AG`) — unlike the `DataBase` sheet's other blocks, none
of these columns are formula-owned (PLAN.md §6.1: "Полный CRUD ботом"), so
every field here is a plain, protected write.
"""

import json
import logging
from dataclasses import replace
from decimal import Decimal

from stalbot.application.dto.delete_item_result import DeleteItemResult
from stalbot.application.ports.clock import Clock
from stalbot.domain.clock import format_datetime
from stalbot.domain.entities.item import Item
from stalbot.domain.enums import ItemCategory
from stalbot.domain.errors import DuplicateItemError, ItemNotFoundError
from stalbot.domain.money import round_for_storage
from stalbot.infrastructure.cache.repositories.boost_order_lines import BoostOrderLinesRepository
from stalbot.infrastructure.cache.repositories.items import (
    ItemsCacheRepository,
    normalize_item_name,
)
from stalbot.infrastructure.cache.repositories.ticket_sessions import TicketSessionsRepository
from stalbot.infrastructure.cache.sync import parse_items_block
from stalbot.infrastructure.sheets.a1 import a1_range
from stalbot.infrastructure.sheets.client import SheetsClient
from stalbot.infrastructure.sheets.layouts import DATA_START_ROW, DATABASE_SHEET, ITEMS_BLOCK

logger = logging.getLogger(__name__)

_ITEMS_RANGE = a1_range(DATABASE_SHEET, ITEMS_BLOCK.col_start, DATA_START_ROW, ITEMS_BLOCK.col_end)


class CatalogService:
    """Adds and removes catalog entries, keeping ids dense and the cache in sync."""

    def __init__(
        self,
        sheets: SheetsClient,
        items: ItemsCacheRepository,
        boost_order_lines: BoostOrderLinesRepository,
        ticket_sessions: TicketSessionsRepository,
        *,
        clock: Clock,
    ) -> None:
        """Wire the service to its collaborators.

        Args:
            sheets: Sheets access (writes are protected + RAW, PLAN.md §7.3).
            items: Cache repository for the item database.
            boost_order_lines: Draft boost-order lines, reassigned/pruned
                when `/del_item` changes an id or removes an item entirely.
            ticket_sessions: Ticket sessions, whose order-editor selection
                (`active_order_item_id`) needs the same reassign/clear
                treatment as `boost_order_lines` when `/del_item` renumbers
                or removes an item (APP-4).
            clock: Time source, tz-aware `GMT3`, stamps `updated_at`.
        """
        self._sheets = sheets
        self._items = items
        self._boost_order_lines = boost_order_lines
        self._ticket_sessions = ticket_sessions
        self._clock = clock

    async def add_item(
        self,
        *,
        name: str,
        category: ItemCategory,
        price_buy: Decimal | None,
        price_sell: Decimal | None,
        emoji: str | None,
    ) -> Item:
        """Add a new catalog entry.

        Args:
            name: Item name.
            category: `RESOURCE` or `BOOST`.
            price_buy: Скуп price, or `None` if not tracked.
            price_sell: Продажа price, or `None` if not tracked.
            emoji: Custom emoji name, or `None`.

        Raises:
            DuplicateItemError: An item with the same name and category
                already exists.
        """
        # Reads the live block, like `delete_item` — not the cache (APP-5):
        # id/row must come from what's actually on the sheet, or two
        # concurrent `/item_add` calls racing an equally-stale cache could
        # compute the same "next" id/row and the second write silently
        # clobbers the first. Reusing the same read for the duplicate check
        # closes the same race there for free.
        catalog = await self._read_block()
        name_norm = normalize_item_name(name)
        if any(
            normalize_item_name(item.name) == name_norm and item.category is category
            for item in catalog
        ):
            raise DuplicateItemError(f"{name} ({category.value})")

        item = Item(
            id=max((i.id for i in catalog), default=0) + 1,
            name=name,
            category=category,
            price_buy=round_for_storage(price_buy) if price_buy is not None else None,
            price_sell=round_for_storage(price_sell) if price_sell is not None else None,
            emoji=emoji,
            updated_at=self._clock.now(),
            row=max((i.row for i in catalog), default=DATA_START_ROW - 1) + 1,
        )
        await self._write_rows([item])
        await self._items.upsert_many([item])
        return item

    async def delete_item(self, item_id: int) -> DeleteItemResult:
        """Remove an item, compacting and renumbering the surviving block.

        Reads the live `item database` block (not the cache) so the
        renumbering can never diverge from what is actually on the sheet
        (PLAN.md §7.5). A JSON snapshot of the block goes to
        `sync_meta` before the rewrite, as a manual-recovery safety net if
        the write fails partway.

        Args:
            item_id: The catalog id to remove.

        Raises:
            ItemNotFoundError: No item with this id exists.
        """
        block = await self._read_block()
        index = next((i for i, item in enumerate(block) if item.id == item_id), None)
        if index is None:
            raise ItemNotFoundError(str(item_id))
        deleted = block[index]

        await self._items.save_delete_backup(
            json.dumps([_item_to_dict(item) for item in block], ensure_ascii=False)
        )

        remaining = block[:index] + block[index + 1 :]
        renumbered = [
            replace(item, id=offset + 1, row=DATA_START_ROW + offset)
            for offset, item in enumerate(remaining)
        ]

        await self._write_rows(renumbered)
        await self._clear_row(DATA_START_ROW + len(renumbered))

        # Clear sessions pointing at the deleted item's id *before* renumbering
        # repoints anything else onto that same now-vacant id — otherwise a
        # session freshly reassigned onto `deleted.id` would be wiped out
        # again by this clear step running after it (APP-4: renumbering can
        # reuse the deleted item's old id as a survivor's new one).
        await self._ticket_sessions.clear_active_order_item_for(deleted.id)

        for before, after in zip(remaining, renumbered, strict=True):
            if before.id != after.id:
                await self._boost_order_lines.reassign_item_id(
                    old_item_id=before.id,
                    new_item_id=after.id,
                    name_norm=normalize_item_name(after.name),
                    category=after.category,
                )
                await self._ticket_sessions.reassign_active_order_item(
                    old_item_id=before.id, new_item_id=after.id
                )
        affected_channels = await self._boost_order_lines.delete_by_name(
            name_norm=normalize_item_name(deleted.name), category=deleted.category
        )

        await self._items.replace_all(renumbered)
        return DeleteItemResult(deleted=deleted, affected_order_channels=affected_channels)

    async def _read_block(self) -> list[Item]:
        result = await self._sheets.batch_get([_ITEMS_RANGE])
        return parse_items_block(result.get(_ITEMS_RANGE, []))

    async def _write_rows(self, items: list[Item]) -> None:
        if not items:
            return
        first_row = items[0].row
        last_row = items[-1].row
        ref = a1_range(
            DATABASE_SHEET, ITEMS_BLOCK.col_start, first_row, ITEMS_BLOCK.col_end, last_row
        )
        await self._sheets.write_verified({ref: [_item_to_row(item) for item in items]})

    async def _clear_row(self, row: int) -> None:
        ref = a1_range(DATABASE_SHEET, ITEMS_BLOCK.col_start, row, ITEMS_BLOCK.col_end, row)
        await self._sheets.batch_update({ref: [["" for _ in range(7)]]})


def _item_to_row(item: Item) -> list[object]:
    return [
        item.id,
        item.name,
        item.category.value,
        int(item.price_buy) if item.price_buy is not None else "",
        int(item.price_sell) if item.price_sell is not None else "",
        item.emoji or "",
        format_datetime(item.updated_at) if item.updated_at is not None else "",
    ]


def _item_to_dict(item: Item) -> dict[str, object]:
    return {
        "id": item.id,
        "name": item.name,
        "category": item.category.value,
        "price_buy": str(item.price_buy) if item.price_buy is not None else None,
        "price_sell": str(item.price_sell) if item.price_sell is not None else None,
        "emoji": item.emoji,
        "updated_at": format_datetime(item.updated_at) if item.updated_at is not None else None,
        "row": item.row,
    }
