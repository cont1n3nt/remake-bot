"""Crafting cost resolution.

Reproduces the shelter workbook's cost formulas exactly (sqlite_migration.md
§II, §V.2, Э5). Pure, no I/O, works in kopecks (`int`) and `Fraction` quantities — never
`float` (`Ед. за крафт` is 4.5/6.25/14.5; float division would drift on
deep recursion and disagree with the sheet on some items).

Resolution order, verified against the sheet (§V.2):

1. `my_kopeks` set -> that value, `source='my_price'`, recursion stops —
   the sheet's `H = G ?? F` / `T = S ?? R`: a manual price always wins and
   short-circuits the recipe lookup entirely.
2. No recipe -> `market_kopeks`, `source='market'`.
3. Otherwise, for every recipe available at the current profession level
   (`units_per_craft > 0`): `ceil(Σ(qty × cost(ingredient)) / units_per_craft)`,
   take the minimum across recipes (`Тех. лист L:T`'s multi-recipe
   resolution), `source='crafted'`.
4. If every recipe failed (cycle, or an ingredient itself unresolved) and
   there's no market price either: `source='unresolved'`.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Final

COST_CALCULATOR_VERSION: Final = 1


@dataclass(frozen=True, slots=True)
class RecipeSpec:
    """One craftable recipe, at the profession's *current* level.

    `units_per_craft` is the yield at that level already — 0 means
    unavailable ("Низкий уровень", §II.2), and the caller is expected to
    have already picked the right `recipe_yields` row for the profession's
    current `level` before building this.
    """

    recipe_id: int
    output_item_id: int
    profession_key: str
    units_per_craft: Fraction
    ingredients: tuple[tuple[int, Fraction], ...]
    """`(shelter_item_id, quantity)` pairs, in recipe order."""


@dataclass(frozen=True, slots=True)
class ItemSpec:
    """One shelter item's price inputs."""

    item_id: int
    kind: str
    """`component` | `craftable` | `virtual` (Энергия)."""
    my_kopeks: int | None
    market_kopeks: int | None


@dataclass(frozen=True, slots=True)
class CostResult:
    """One item's resolved cost."""

    cost_kopeks: int | None
    """`None` only when `source == 'unresolved'`."""
    best_recipe_id: int | None
    """Set only when `source == 'crafted'`."""
    source: str
    depth: int
    note: str | None


class _CycleError(Exception):
    """Internal signal: resolving this item would recurse back into itself."""

    def __init__(self, item_id: int) -> None:
        super().__init__(item_id)
        self.item_id = item_id


class _UnresolvedIngredientError(Exception):
    """Internal signal: an ingredient of the current recipe has no cost."""


def _ceil_fraction(value: Fraction) -> int:
    whole, remainder = divmod(value.numerator, value.denominator)
    return whole + 1 if remainder else whole


def compute_costs(
    items: Mapping[int, ItemSpec], recipes: Sequence[RecipeSpec]
) -> dict[int, CostResult]:
    """Resolve every item's cost — components, craftables, and Энергия alike.

    Args:
        items: Every shelter item, keyed by id. An item with no recipe and
            no price simply resolves to `unresolved`.
        recipes: Every recipe available at the professions' current
            levels (already yield-filtered per level by the caller — see
            `RecipeSpec.units_per_craft`).

    Returns:
        One `CostResult` per key in `items`.
    """
    recipes_by_output: dict[int, list[RecipeSpec]] = {}
    for recipe in recipes:
        recipes_by_output.setdefault(recipe.output_item_id, []).append(recipe)

    memo: dict[int, CostResult] = {}

    def resolve(item_id: int, visiting: frozenset[int], depth: int) -> CostResult:
        if item_id in memo:
            return memo[item_id]
        item = items.get(item_id)
        if item is None:
            result = CostResult(None, None, "unresolved", depth, f"unknown item {item_id}")
            memo[item_id] = result
            return result

        if item.my_kopeks is not None:
            result = CostResult(item.my_kopeks, None, "my_price", depth, None)
            memo[item_id] = result
            return result

        available_recipes = [r for r in recipes_by_output.get(item_id, ()) if r.units_per_craft > 0]
        if not available_recipes:
            result = (
                CostResult(item.market_kopeks, None, "market", depth, None)
                if item.market_kopeks is not None
                else CostResult(None, None, "unresolved", depth, "no recipe and no market price")
            )
            memo[item_id] = result
            return result

        if item_id in visiting:
            raise _CycleError(item_id)

        candidates: list[tuple[Fraction, int]] = []
        notes: list[str] = []
        for recipe in available_recipes:
            try:
                total = Fraction(0)
                for ingredient_id, quantity in recipe.ingredients:
                    ingredient_result = resolve(ingredient_id, visiting | {item_id}, depth + 1)
                    if ingredient_result.cost_kopeks is None:
                        raise _UnresolvedIngredientError
                    total += quantity * ingredient_result.cost_kopeks
                candidates.append((total / recipe.units_per_craft, recipe.recipe_id))
            except _CycleError as exc:
                notes.append(f"cycle: item {exc.item_id} via recipe {recipe.recipe_id}")
            except _UnresolvedIngredientError:
                notes.append(f"unresolved ingredient via recipe {recipe.recipe_id}")

        if candidates:
            best_fraction, best_recipe_id = min(candidates, key=lambda c: c[0])
            cost = _ceil_fraction(best_fraction)
            result = CostResult(cost, best_recipe_id, "crafted", depth, None)
        elif item.market_kopeks is not None:
            result = CostResult(item.market_kopeks, None, "market", depth, "; ".join(notes) or None)
        else:
            result = CostResult(
                None, None, "unresolved", depth, "; ".join(notes) or "no recipe resolved"
            )
        memo[item_id] = result
        return result

    for item_id in items:
        resolve(item_id, frozenset(), 0)
    return memo
