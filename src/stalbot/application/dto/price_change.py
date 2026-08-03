"""`PriceChange` — one item's before/after price, shared by every pricing command.

`/setprice`, `/setboost`, `/new_price` and `/sync_prices` (PLAN.md §10.6-§10.8)
all end in the same report format — this is the one shape they build it from.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from stalbot.domain.entities.item import Item
from stalbot.domain.enums import ItemCategory, PriceField


@dataclass(frozen=True, slots=True)
class PriceChange:
    """One item's price move on one field (buy or sell)."""

    item_id: int
    item_name: str
    category: ItemCategory
    field: PriceField
    old_price: Decimal | None
    new_price: Decimal | None


@dataclass(frozen=True, slots=True)
class GroupedPriceChanges:
    """A price-change report split into the three blocks PLAN.md §10.6 shows.

    "Скуп бустов" is not a stored category — it is a `RESOURCE` entry whose
    name collides with a `BOOST` entry's name (PLAN.md §10.6's footnote).
    """

    resources: tuple[PriceChange, ...] = ()
    boosts: tuple[PriceChange, ...] = ()
    boost_scalp: tuple[PriceChange, ...] = ()


def group_price_changes(
    changes: Sequence[PriceChange], catalog: Sequence[Item]
) -> GroupedPriceChanges:
    """Split a flat list of price changes into the resource/boost/scalp report blocks.

    Args:
        changes: Every price change to report.
        catalog: The full item catalog, used to detect a resource/boost
            name collision (the "скуп бустов" case).
    """
    boost_names = {_name_key(item.name) for item in catalog if item.category is ItemCategory.BOOST}
    resources: list[PriceChange] = []
    boosts: list[PriceChange] = []
    boost_scalp: list[PriceChange] = []
    for change in changes:
        if change.category is ItemCategory.BOOST:
            boosts.append(change)
        elif _name_key(change.item_name) in boost_names:
            boost_scalp.append(change)
        else:
            resources.append(change)
    return GroupedPriceChanges(
        resources=tuple(resources), boosts=tuple(boosts), boost_scalp=tuple(boost_scalp)
    )


def _name_key(name: str) -> str:
    return " ".join(name.split()).lower()
