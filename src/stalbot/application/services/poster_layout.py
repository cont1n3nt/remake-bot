"""Editing what is on a poster (заявка 13.09.2026 п.6, вторая половина п.13).

Backs the `/poster_item` group and the poster half of `/item_add`. Owns the
two-sided consistency the layout needs: a slot points at an icon file, and
that file must exist while the slot does and must not linger once nothing
references it.

Everything here refuses to silently do the wrong thing rather than guess —
adding an item that is already on the poster, pointing a slot at a picture
that failed to decode, moving a slot onto a poster it does not belong to.
The whole point of the feature is that the owner can trust the poster
matches what they asked for without re-rendering to check.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from stalbot.application.ports.clock import Clock
from stalbot.application.ports.icon_store import IconStore
from stalbot.application.services.posters import CATEGORY_BY_KIND
from stalbot.domain.entities.catalog_item import CatalogItem
from stalbot.domain.entities.poster_layout import PosterSectionRow, PosterSlotRow
from stalbot.domain.enums import PosterKind
from stalbot.domain.errors import DomainError, ItemNotFoundError
from stalbot.infrastructure.cache.repositories.catalog_items import CatalogItemsRepository
from stalbot.infrastructure.cache.repositories.poster_layout import PosterLayoutRepository


class ItemAlreadyOnPosterError(DomainError):
    """The item already occupies a slot on this poster."""


class WrongPosterCategoryError(DomainError):
    """The item's catalog category does not belong on this poster."""


class SlotNotFoundError(DomainError):
    """No slot for this item on this poster."""


@dataclass(frozen=True, slots=True)
class PosterEntry:
    """One slot as `/poster_item list` shows it."""

    slot: PosterSlotRow
    section: PosterSectionRow
    item: CatalogItem | None
    """`None` when the slot resolves to nothing in the catalog — it will be
    skipped when the poster renders, and that is worth surfacing."""
    icon_present: bool

    @property
    def is_healthy(self) -> bool:
        """Whether this slot will actually draw."""
        return self.item is not None and self.icon_present and _price_of(self.item) is not None


@dataclass(frozen=True, slots=True)
class PosterOverview:
    """A whole poster's slots, plus what the catalog has that is missing from it."""

    entries: tuple[PosterEntry, ...]
    missing_from_poster: tuple[CatalogItem, ...]
    """Priced catalog items of this poster's category with no slot — the
    answer to "почему этого нет на плакате"."""


class PosterLayoutService:
    """Adds, re-pictures, moves and removes poster slots."""

    def __init__(
        self,
        layout: PosterLayoutRepository,
        catalog_items: CatalogItemsRepository,
        icons: IconStore,
        *,
        clock: Clock,
    ) -> None:
        """Wire the service to its collaborators.

        Args:
            layout: Cache repository for `poster_sections`/`poster_slots`.
            catalog_items: Resolves the item a slot is about.
            icons: Stores and removes the icon files themselves.
            clock: Time source, tz-aware `GMT3`.
        """
        self._layout = layout
        self._catalog_items = catalog_items
        self._icons = icons
        self._clock = clock

    async def add_item(
        self,
        kind: PosterKind,
        item_id: int,
        *,
        section_name: str | None,
        icon_data: bytes,
    ) -> PosterSlotRow:
        """Put a catalog item on a poster, with the supplied picture.

        Args:
            kind: Which poster.
            item_id: The catalog item to add.
            section_name: Section to append to — created if it does not
                exist yet. `None` starts a new unnamed block.
            icon_data: Raw uploaded image bytes.

        Raises:
            ItemNotFoundError: No such catalog item.
            WrongPosterCategoryError: The item is on the other side of the trade.
            ItemAlreadyOnPosterError: It already has a slot here.
            IconRejectedError: The upload is unusable.
        """
        item = await self._require_item(kind, item_id)
        if await self._layout.find_slot(kind, item_id) is not None:
            raise ItemAlreadyOnPosterError(f"«{item.name}» уже есть на этом плакате")

        now = self._clock.now()
        section = await self._layout.get_or_create_section(kind, section_name, now=now)
        assert section.id is not None  # noqa: S101 - just persisted
        # The icon is written before the slot on purpose: a saved file with
        # no slot is invisible clutter, a slot pointing at a file that was
        # never written is a blank square on the poster.
        icon_file = self._icons.save(icon_data, name_norm=item.name_norm)
        slot_id = await self._layout.add_slot(
            PosterSlotRow(
                id=None,
                section_id=section.id,
                sort_order=await self._layout.next_sort_order(section.id),
                catalog_item_id=item_id,
                display_name=item.name,
                name_norm=item.name_norm,
                icon_file=icon_file,
            ),
            now=now,
        )
        slots = await self._layout.slots(section.id)
        return next(slot for slot in slots if slot.id == slot_id)

    async def set_icon(self, kind: PosterKind, item_id: int, *, icon_data: bytes) -> PosterSlotRow:
        """Replace the picture of an item already on a poster.

        Args:
            kind: Which poster.
            item_id: The catalog item whose slot to re-picture.
            icon_data: Raw uploaded image bytes.

        Raises:
            SlotNotFoundError: The item is not on this poster.
            IconRejectedError: The upload is unusable.
        """
        item = await self._require_item(kind, item_id)
        slot = await self._require_slot(kind, item_id, item.name)
        assert slot.id is not None  # noqa: S101 - a fetched slot always has an id

        icon_file = self._icons.save(icon_data, name_norm=item.name_norm)
        await self._layout.set_icon(slot.id, icon_file, now=self._clock.now())
        await self._drop_icon_if_orphaned(slot.icon_file)
        return PosterSlotRow(
            id=slot.id,
            section_id=slot.section_id,
            sort_order=slot.sort_order,
            catalog_item_id=slot.catalog_item_id,
            display_name=slot.display_name,
            name_norm=slot.name_norm,
            icon_file=icon_file,
            created_at=slot.created_at,
        )

    async def move_item(
        self, kind: PosterKind, item_id: int, *, section_name: str | None, position: int | None
    ) -> PosterSlotRow:
        """Move an item's slot to another section and/or position.

        Args:
            kind: Which poster.
            item_id: The catalog item to move.
            section_name: Target section, or `None` to stay in the current one.
            position: 1-based position within the section, or `None` for the end.

        Raises:
            SlotNotFoundError: The item is not on this poster.
        """
        item = await self._require_item(kind, item_id)
        slot = await self._require_slot(kind, item_id, item.name)
        assert slot.id is not None  # noqa: S101 - a fetched slot always has an id

        now = self._clock.now()
        if section_name is None:
            section_id = slot.section_id
        else:
            section = await self._layout.get_or_create_section(kind, section_name, now=now)
            assert section.id is not None  # noqa: S101 - just persisted
            section_id = section.id

        siblings = [s for s in await self._layout.slots(section_id) if s.id != slot.id]
        index = len(siblings) if position is None else max(0, min(len(siblings), position - 1))
        reordered = [*siblings[:index], slot, *siblings[index:]]
        for order, entry in enumerate(reordered):
            assert entry.id is not None  # noqa: S101 - fetched slots always have ids
            await self._layout.move_slot(entry.id, section_id=section_id, sort_order=order, now=now)
        return PosterSlotRow(
            id=slot.id,
            section_id=section_id,
            sort_order=index,
            catalog_item_id=slot.catalog_item_id,
            display_name=slot.display_name,
            name_norm=slot.name_norm,
            icon_file=slot.icon_file,
            created_at=slot.created_at,
        )

    async def remove_item(self, kind: PosterKind, item_id: int) -> PosterSlotRow:
        """Take an item off a poster, leaving the catalog untouched.

        Args:
            kind: Which poster.
            item_id: The catalog item to remove.

        Raises:
            SlotNotFoundError: The item is not on this poster.
        """
        item = await self._require_item(kind, item_id, check_category=False)
        slot = await self._require_slot(kind, item_id, item.name if item else str(item_id))
        assert slot.id is not None  # noqa: S101 - a fetched slot always has an id
        removed = await self._layout.delete_slot(slot.id)
        assert removed is not None  # noqa: S101 - fetched a moment ago
        await self._drop_icon_if_orphaned(removed.icon_file)
        return removed

    async def remove_every_slot_for(self, item_id: int) -> int:
        """Drop every slot for a catalog item, across all posters.

        Called when the item itself is deleted from the catalog: a slot
        whose item is gone renders as nothing, so leaving it behind only
        hides the fact that the poster shrank.

        Args:
            item_id: The catalog item being removed.

        Returns:
            How many slots were dropped.
        """
        slots = await self._layout.slots_for_item(item_id)
        if not slots:
            return 0
        removed = await self._layout.delete_slots_for_item(item_id)
        for slot in slots:
            await self._drop_icon_if_orphaned(slot.icon_file)
        return removed

    async def overview(self, kind: PosterKind) -> PosterOverview:
        """Everything on one poster, plus priced catalog items that are missing from it.

        Args:
            kind: Which poster to describe.
        """
        category = CATEGORY_BY_KIND[kind]
        catalog = [
            item
            for item in await self._catalog_items.all()
            if item.category is category and item.deleted_at is None
        ]
        by_id = {item.id: item for item in catalog if item.id is not None}
        by_name_norm = {item.name_norm: item for item in catalog}

        entries: list[PosterEntry] = []
        placed: set[int] = set()
        for section, slots in await self._layout.layout(kind):
            for slot in slots:
                item = (
                    by_id.get(slot.catalog_item_id) if slot.catalog_item_id is not None else None
                ) or by_name_norm.get(slot.name_norm)
                if item is not None and item.id is not None:
                    placed.add(item.id)
                entries.append(
                    PosterEntry(
                        slot=slot,
                        section=section,
                        item=item,
                        icon_present=self._icons.exists(slot.icon_file),
                    )
                )

        missing = [
            item for item in catalog if item.id not in placed and _price_of(item) is not None
        ]
        missing.sort(key=lambda item: item.name.casefold())
        return PosterOverview(entries=tuple(entries), missing_from_poster=tuple(missing))

    async def sections(self, kind: PosterKind) -> Sequence[PosterSectionRow]:
        """Existing sections of a poster, for the section autocomplete.

        Args:
            kind: Which poster.
        """
        return await self._layout.sections(kind)

    # --- internals ---------------------------------------------------------

    async def _require_item(
        self, kind: PosterKind, item_id: int, *, check_category: bool = True
    ) -> CatalogItem:
        item = await self._catalog_items.get_by_id(item_id)
        if item is None or item.deleted_at is not None:
            raise ItemNotFoundError(str(item_id))
        if check_category and item.category is not CATEGORY_BY_KIND[kind]:
            raise WrongPosterCategoryError(
                f"«{item.name}» — это {item.category.value}, "
                "а на этом плакате другая сторона сделки"  # noqa: RUF001
            )
        return item

    async def _require_slot(self, kind: PosterKind, item_id: int, name: str) -> PosterSlotRow:
        slot = await self._layout.find_slot(kind, item_id)
        if slot is None:
            raise SlotNotFoundError(f"«{name}» нет на этом плакате")
        return slot

    async def _drop_icon_if_orphaned(self, icon_file: str) -> None:
        self._icons.delete_if_unused(icon_file, still_used=await self._layout.icon_files())


def _price_of(item: CatalogItem) -> int | None:
    return item.price_buy if item.price_buy is not None else item.price_sell
