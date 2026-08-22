"""Fuzzy item-name autocomplete, shared by every command with an item argument.

`/setprice`, `/setboost`, `/del_item` (PLAN.md §10.7, §10.9) all offer the
same experience: type part of a name, get up to 25 ranked matches, the
option's actual value is the catalog `item_id` (not the name) so a duplicate
name across categories never resolves ambiguously.
"""

from collections.abc import Sequence
from difflib import SequenceMatcher

from discord import app_commands

from stalbot.domain.entities.catalog_item import CatalogItem
from stalbot.domain.entities.shelter_item import ShelterItem
from stalbot.domain.enums import ItemCategory
from stalbot.domain.money import format_amount

_MAX_CHOICES = 25
_MIN_SCORE = 0.3
_CHOICE_NAME_MAX = 100


def item_choices(
    items: Sequence[CatalogItem], query: str, *, category: ItemCategory | None = None
) -> list[app_commands.Choice[int]]:
    """Rank a catalog by *query* and build up to 25 autocomplete choices.

    Args:
        items: The full cached catalog, or a category-scoped slice.
        query: What the user has typed so far into the option.
        category: Restrict results to one category, or `None` for both.

    Returns:
        Choices whose `value` is the matching item's `id` and whose `name`
        is a human label (name + current price), ranked best-match-first.
    """
    candidates = [
        item
        for item in items
        if item.id is not None and (category is None or item.category is category)
    ]
    if not query:
        ranked = candidates
    else:
        scored = [(item, _score(query, item.name)) for item in candidates]
        ranked = [item for item, score in scored if score >= _MIN_SCORE]
        ranked.sort(key=lambda item: _score(query, item.name), reverse=True)

    choices: list[app_commands.Choice[int]] = []
    for item in ranked[:_MAX_CHOICES]:
        assert item.id is not None  # noqa: S101 - filtered above
        choices.append(app_commands.Choice(name=_label(item)[:_CHOICE_NAME_MAX], value=item.id))
    return choices


def shelter_item_choices(items: Sequence[ShelterItem], query: str) -> list[app_commands.Choice[int]]:
    """Rank shelter items by *query*, for `/cost` and `/precost` (§V.2).

    Same fuzzy-match ranking as `item_choices`, but over `shelter_items` —
    421 crafting-reference entries with no category/catalog price, so the
    choice label is just the name.

    Args:
        items: The full cached shelter item list.
        query: What the user has typed so far into the option.
    """
    candidates = [item for item in items if item.id is not None]
    if not query:
        ranked = candidates
    else:
        scored = [(item, _score(query, item.name)) for item in candidates]
        ranked = [item for item, score in scored if score >= _MIN_SCORE]
        ranked.sort(key=lambda item: _score(query, item.name), reverse=True)

    choices: list[app_commands.Choice[int]] = []
    for item in ranked[:_MAX_CHOICES]:
        assert item.id is not None  # noqa: S101 - filtered above
        choices.append(app_commands.Choice(name=item.name[:_CHOICE_NAME_MAX], value=item.id))
    return choices


def _score(query: str, name: str) -> float:
    """Rank *name* against *query*: an exact substring beats a fuzzy match."""
    query_norm = query.casefold()
    name_norm = name.casefold()
    if query_norm in name_norm:
        return 1.0
    return SequenceMatcher(None, query_norm, name_norm).ratio()


def _label(item: CatalogItem) -> str:
    price = item.price_buy if item.category is ItemCategory.RESOURCE else item.price_sell
    price_text = format_amount(price) if price is not None else "цены нет"
    return f"{item.name} — {price_text}"
