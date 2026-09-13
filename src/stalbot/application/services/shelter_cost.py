"""`ShelterCostService` — cost-of-goods over the shelter crafting model (§V.2).

`current_costs` (`/cost`) and `precost` (`/precost`) are read-only previews
over `domain.shelter.cost`; neither writes anything back.

`recompute` is the one write, and it exists because the recipe commands
(заявка 13.09.2026 п.3) changed who edits this data. `shelter_cost` used to
be materialized only by `scripts/recompute_shelter_costs.py`, run by hand
after an import — fine while recipes only changed through an import. Now
the owner edits a recipe from Discord and expects `/cost` to agree a second
later, so the same computation lives here and runs after every edit.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace

from stalbot.application.dto.precost_diff import PrecostDiff
from stalbot.application.ports.clock import Clock
from stalbot.domain.errors import ItemNotFoundError
from stalbot.domain.shelter.cost import CostResult, compute_costs
from stalbot.infrastructure.cache.repositories.shelter import ShelterRepository


@dataclass(frozen=True, slots=True)
class RecomputeReport:
    """What one `recompute()` resolved."""

    items: int
    unresolved: int
    """Items left with no cost at all — no price, and no recipe that resolves."""


class ShelterCostService:
    """Cost-of-goods lookups, and the one job that materializes them."""

    def __init__(self, shelter: ShelterRepository, *, clock: Clock | None = None) -> None:
        """Wire the service to its collaborator.

        Args:
            shelter: Cache repository for `shelter_items`/`recipes`.
            clock: Time source for `recompute`'s `computed_at` stamp.
                Optional only so the two read-only callers need not supply
                one; `recompute` requires it.
        """
        self._shelter = shelter
        self._clock = clock

    async def recompute(self) -> RecomputeReport:
        """Recompute and persist every item's cost.

        Called after any recipe, price, level or setting change, so `/cost`
        never reports a number the current data no longer supports.

        Raises:
            RuntimeError: The service was built without a clock.
        """
        if self._clock is None:
            raise RuntimeError("ShelterCostService needs a clock to recompute costs")
        items = await self._shelter.load_item_specs()
        recipes = await self._shelter.load_recipe_specs_for_current_levels()
        results = compute_costs(items, recipes)
        await self._shelter.save_costs(results, now=self._clock.now())
        unresolved = sum(1 for result in results.values() if result.source == "unresolved")
        return RecomputeReport(items=len(results), unresolved=unresolved)

    async def current_costs(self) -> dict[int, CostResult]:
        """Resolve every shelter item's cost at its current stored price.

        Live-computed rather than read from the materialized `shelter_cost`
        table, so it can never be stale relative to a price/recipe edit
        that hasn't had `scripts/recompute_shelter_costs.py` run since.
        """
        items = await self._shelter.load_item_specs()
        recipes = await self._shelter.load_recipe_specs_for_current_levels()
        return compute_costs(items, recipes)

    async def precost(self, overrides: Mapping[int, int]) -> list[PrecostDiff]:
        """Preview which items' cost would change at the given hypothetical prices.

        Nothing is written — this computes the full cost graph twice (once
        at the current stored prices, once with every `my_kopeks` in
        *overrides* replaced) and returns every item whose resolved cost
        differs between the two runs. A plain diff of two full
        `compute_costs` runs already covers every downstream consumer,
        direct or transitive, so no separate "what uses this ingredient"
        index is needed — and that is exactly why several overrides cost no
        more than one (заявка 13.09.2026 п.12: preview a whole price move,
        not one ingredient at a time). Two ingredients of the same craft
        moving together can even cancel out, which one-at-a-time previews
        can't show.

        Args:
            overrides: `shelter_items.id -> hypothetical my_kopeks`, at
                least one entry.

        Raises:
            ItemNotFoundError: An id in *overrides* is not a known shelter item.
        """
        items = await self._shelter.load_item_specs()
        for item_id in overrides:
            if item_id not in items:
                raise ItemNotFoundError(str(item_id))
        recipes = await self._shelter.load_recipe_specs_for_current_levels()

        baseline = compute_costs(items, recipes)
        hypothetical_items = dict(items)
        for item_id, new_my_kopeks in overrides.items():
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
