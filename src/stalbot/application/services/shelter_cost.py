"""`ShelterCostService` — backs `/cost` and `/precost` (sqlite_migration.md §V.2).

Both commands are read-only previews over the same `domain.shelter.cost`
engine `scripts/recompute_shelter_costs.py` already uses to materialize
`shelter_cost` — this service never writes anything back to the database,
it only calls `compute_costs` against the live data (`/cost`) or against a
hypothetical one-item price override (`/precost`).
"""

from dataclasses import replace

from stalbot.application.dto.precost_diff import PrecostDiff
from stalbot.domain.errors import ItemNotFoundError
from stalbot.domain.shelter.cost import CostResult, compute_costs
from stalbot.infrastructure.cache.repositories.shelter import ShelterRepository


class ShelterCostService:
    """Read-only cost-of-goods lookups over the shelter crafting model."""

    def __init__(self, shelter: ShelterRepository) -> None:
        """Wire the service to its collaborator.

        Args:
            shelter: Cache repository for `shelter_items`/`recipes`.
        """
        self._shelter = shelter

    async def current_costs(self) -> dict[int, CostResult]:
        """Resolve every shelter item's cost at its current stored price.

        Live-computed rather than read from the materialized `shelter_cost`
        table, so it can never be stale relative to a price/recipe edit
        that hasn't had `scripts/recompute_shelter_costs.py` run since.
        """
        items = await self._shelter.load_item_specs()
        recipes = await self._shelter.load_recipe_specs_for_current_levels()
        return compute_costs(items, recipes)

    async def precost(self, item_id: int, new_my_kopeks: int) -> list[PrecostDiff]:
        """Preview which items' cost would change if *item_id* cost *new_my_kopeks*.

        Nothing is written — this computes the full cost graph twice (once
        at the current stored price, once with `item_id.my_kopeks`
        overridden) and returns every item whose resolved cost differs
        between the two runs. A plain diff of two full `compute_costs` runs
        already covers every downstream consumer, direct or transitive, so
        no separate "what uses this ingredient" index is needed.

        Args:
            item_id: The `shelter_items.id` whose price is being previewed.
            new_my_kopeks: The hypothetical `my_kopeks` value.

        Raises:
            ItemNotFoundError: `item_id` is not a known shelter item.
        """
        items = await self._shelter.load_item_specs()
        if item_id not in items:
            raise ItemNotFoundError(str(item_id))
        recipes = await self._shelter.load_recipe_specs_for_current_levels()

        baseline = compute_costs(items, recipes)
        hypothetical_items = dict(items)
        hypothetical_items[item_id] = replace(items[item_id], my_kopeks=new_my_kopeks)
        hypothetical = compute_costs(hypothetical_items, recipes)

        names = {item.id: item.name for item in await self._shelter.all_items()}
        diffs = [
            PrecostDiff(
                item_id=affected_id,
                item_name=names.get(affected_id, f"#{affected_id}"),
                before_kopeks=before.cost_kopeks,
                after_kopeks=hypothetical[affected_id].cost_kopeks,
            )
            for affected_id, before in baseline.items()
            if before.cost_kopeks != hypothetical[affected_id].cost_kopeks
        ]
        diffs.sort(key=lambda diff: diff.item_name)
        return diffs
