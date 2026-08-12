"""Item database CRUD (PLAN.md §10.9, §7.5; sqlite_migration.md §III.3, Э7).

`/item_add` and `/del_item` write straight to `catalog_items` now — no more
Sheets block, no more dense id renumbering on every delete. An id is a
surrogate, assigned once and never reused (§III.3: "id не перенумеровываются");
`/del_item` soft-deletes (`deleted_at`), so a draft boost-order line or a
ticket session's editor selection referencing that id never needs
repointing — only pruning, and only for the one id that actually vanished.
"""

from decimal import Decimal

from stalbot.application.dto.delete_item_result import DeleteItemResult
from stalbot.application.ports.clock import Clock
from stalbot.domain.entities.catalog_item import CatalogItem
from stalbot.domain.enums import ItemCategory
from stalbot.domain.errors import DuplicateItemError, InvalidCategoryPriceError, ItemNotFoundError
from stalbot.domain.money import to_storage
from stalbot.infrastructure.cache.repositories.boost_order_lines import BoostOrderLinesRepository
from stalbot.infrastructure.cache.repositories.catalog_items import CatalogItemsRepository
from stalbot.infrastructure.cache.repositories.items import normalize_item_name


class CatalogService:
    """Adds and removes catalog entries."""

    def __init__(
        self,
        items: CatalogItemsRepository,
        boost_order_lines: BoostOrderLinesRepository,
        *,
        clock: Clock,
    ) -> None:
        """Wire the service to its collaborators.

        Args:
            items: Cache repository for the item catalog.
            boost_order_lines: Draft boost-order lines, pruned when
                `/del_item` removes an item they reference.
            clock: Time source, tz-aware `GMT3`, stamps `created_at`/`updated_at`.
        """
        self._items = items
        self._boost_order_lines = boost_order_lines
        self._clock = clock

    async def add_item(
        self,
        *,
        name: str,
        category: ItemCategory,
        price_buy: Decimal | None,
        price_sell: Decimal | None,
        emoji: str | None,
    ) -> CatalogItem:
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
            InvalidCategoryPriceError: A resource was given a sell price,
                or a boost a buy price (§I.5: category is the trade side).
        """
        if category is ItemCategory.RESOURCE and price_sell is not None:
            raise InvalidCategoryPriceError("resource cannot have a sell price")
        if category is ItemCategory.BOOST and price_buy is not None:
            raise InvalidCategoryPriceError("boost cannot have a buy price")

        name_norm = normalize_item_name(name)
        if await self._items.find(name_norm, category) is not None:
            raise DuplicateItemError(f"{name} ({category.value})")

        now = self._clock.now()
        item = CatalogItem(
            id=None,
            name=name,
            name_norm=name_norm,
            category=category,
            section=None,
            price_buy=to_storage(price_buy) if price_buy is not None else None,
            price_sell=to_storage(price_sell) if price_sell is not None else None,
            emoji=emoji,
            sort_order=0,
            shelter_item_id=None,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        return await self._items.insert(item)

    async def delete_item(self, item_id: int) -> DeleteItemResult:
        """Soft-delete an item.

        Args:
            item_id: The catalog id to remove.

        Raises:
            ItemNotFoundError: No active item with this id exists.
        """
        item = await self._items.get_by_id(item_id)
        if item is None or item.deleted_at is not None:
            raise ItemNotFoundError(str(item_id))

        now = self._clock.now()
        await self._items.soft_delete(item_id, now=now)
        affected_channels = await self._boost_order_lines.delete_by_name(
            name_norm=item.name_norm, category=item.category
        )
        return DeleteItemResult(deleted=item, affected_order_channels=affected_channels)
