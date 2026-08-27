"""`/temp_price` — a price override that expires and reverts itself (заявка 21.08.2026 п.9).

Reuses `catalog_items`/`item_price_history` — the live override is a plain
price write, same column `/setprice` writes, logged with
`PriceChangeSource.TEMP_PRICE` instead of `SETPRICE` so both the apply and
the automatic revert are distinguishable in `item_price_history`.
"""

from datetime import datetime
from decimal import Decimal

from stalbot.application.dto.price_change import PriceChange
from stalbot.application.ports.clock import Clock
from stalbot.domain.entities.item_price_history import ItemPriceHistoryEntry
from stalbot.domain.entities.temp_price import TempPrice
from stalbot.domain.enums import PriceChangeSource, PriceField
from stalbot.domain.errors import ItemNotFoundError
from stalbot.domain.money import to_storage
from stalbot.infrastructure.cache.repositories.catalog_items import CatalogItemsRepository
from stalbot.infrastructure.cache.repositories.item_price_history import ItemPriceHistoryRepository
from stalbot.infrastructure.cache.repositories.temp_prices import TempPricesRepository


class TempPriceService:
    """Applies and auto-reverts temporary price overrides."""

    def __init__(
        self,
        temp_prices: TempPricesRepository,
        items: CatalogItemsRepository,
        history: ItemPriceHistoryRepository,
        *,
        clock: Clock,
    ) -> None:
        """Wire the service to its collaborators.

        Args:
            temp_prices: Cache repository for `temp_prices`.
            items: Cache repository for the item catalog.
            history: Append-only log of every price change.
            clock: Time source, tz-aware `GMT3`, stamps `created_at`/compares `expires_at`.
        """
        self._temp_prices = temp_prices
        self._items = items
        self._history = history
        self._clock = clock

    async def set_temp_price(
        self,
        item_id: int,
        field: PriceField,
        amount: Decimal,
        until: datetime,
        *,
        changed_by: int | None,
    ) -> PriceChange:
        """Set a price that reverts to its current value once `until` passes.

        Args:
            item_id: Catalog id of the item to override.
            field: Which price to override.
            amount: The temporary price.
            until: When the override should revert.
            changed_by: Discord id of the admin setting it.

        Raises:
            ItemNotFoundError: No item with this id exists.
        """
        item = await self._items.get_by_id(item_id)
        if item is None:
            raise ItemNotFoundError(str(item_id))

        # Re-applying before the previous override expired must not lose the
        # *true* original — reuse it and drop the superseded row instead of
        # capturing the still-temporary current price as if it were real.
        existing = await self._temp_prices.get_active(item_id, field)
        if existing is not None:
            original_price = existing.original_price
            assert existing.id is not None  # noqa: S101 - a fetched row always has an id
            await self._temp_prices.delete(existing.id)
        else:
            original_price = item.price_buy if field is PriceField.BUY else item.price_sell

        now = self._clock.now()
        change = await self._apply(
            item_id, field, to_storage(amount), changed_by=changed_by, now=now
        )
        await self._temp_prices.create(
            item_id, field, original_price, until, created_by=changed_by, now=now
        )
        return change

    async def list_active(self) -> list[TempPrice]:
        """Return every temp override currently in effect, soonest-expiring first.

        заявка 27.08.2026 п.8.
        """
        return list(await self._temp_prices.all())

    async def revert_due(self) -> list[PriceChange]:
        """Revert every temp override whose `expires_at` has passed, logging each one."""
        now = self._clock.now()
        due = await self._temp_prices.list_due(now)
        reverted: list[PriceChange] = []
        for row in due:
            assert row.id is not None  # noqa: S101 - a fetched row always has an id
            change = await self._apply(
                row.item_id, row.field, row.original_price, changed_by=None, now=now
            )
            await self._temp_prices.delete(row.id)
            reverted.append(change)
        return reverted

    async def _apply(
        self,
        item_id: int,
        field: PriceField,
        new_price: int | None,
        *,
        changed_by: int | None,
        now: datetime,
    ) -> PriceChange:
        """Write one price field directly and log it under `TEMP_PRICE` (apply or revert)."""
        item = await self._items.get_by_id(item_id)
        assert item is not None  # noqa: S101 - caller already resolved this item this call

        old_price = item.price_buy if field is PriceField.BUY else item.price_sell
        price_buy = new_price if field is PriceField.BUY else item.price_buy
        price_sell = new_price if field is PriceField.SELL else item.price_sell
        await self._items.set_price(item_id, price_buy=price_buy, price_sell=price_sell, now=now)
        await self._history.add(
            ItemPriceHistoryEntry(
                id=None,
                item_id=item_id,
                field=field,
                old_price=old_price,
                new_price=new_price,
                changed_by=changed_by,
                source=PriceChangeSource.TEMP_PRICE,
                changed_at=now,
            )
        )
        return PriceChange(
            item_id=item_id,
            item_name=item.name,
            category=item.category,
            field=field,
            old_price=old_price,
            new_price=new_price,
        )
