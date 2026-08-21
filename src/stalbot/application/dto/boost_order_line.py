"""`BoostOrderLine` — one item in an in-progress boost order draft (PLAN.md §8.1, §11.6).

Has no Sheets counterpart. `item_name_norm`/`category` accompany `item_id`
so `CatalogService.delete_item` can prune every draft line referencing a
removed item by name (`BoostOrderLinesRepository.delete_by_name`) without a
join back to `catalog_items` — `item_id` itself is a stable surrogate
(sqlite_migration.md §III.3) and never needs repointing.
"""

from dataclasses import dataclass

from stalbot.domain.enums import ItemCategory


@dataclass(frozen=True, slots=True)
class BoostOrderLine:
    """One selected boost and its quantity within a channel's draft order."""

    channel_id: int
    item_id: int
    item_name_norm: str
    category: ItemCategory
    quantity: int

    def __post_init__(self) -> None:
        """Reject a non-positive quantity (mirrors the schema's `CHECK`)."""
        if self.quantity <= 0:
            raise ValueError(f"quantity must be positive, got {self.quantity}")
