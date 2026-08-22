"""`PrecostDiff` — one item's cost-of-goods before/after a hypothetical price (`/precost`)."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PrecostDiff:
    """One shelter item whose resolved cost moves under the hypothetical price."""

    item_id: int
    item_name: str
    before_kopeks: int | None
    after_kopeks: int | None
