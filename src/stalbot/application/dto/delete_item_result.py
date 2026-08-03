"""`DeleteItemResult` — what `/del_item` actually did (PLAN.md §7.5, §10.9)."""

from collections.abc import Sequence
from dataclasses import dataclass, field

from stalbot.domain.entities.item import Item


@dataclass(frozen=True, slots=True)
class DeleteItemResult:
    """The removed item, plus any boost-order drafts that referenced it."""

    deleted: Item
    """The item as it existed right before deletion (for the confirmation embed)."""
    affected_order_channels: Sequence[int] = field(default_factory=tuple)
    """Channel ids whose draft boost order had a line for this item removed."""
