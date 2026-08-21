"""One-off: recompute and save `shelter_cost` from the current shelter data.

`shelter_cost` is only ever written when something explicitly recomputes
it — `import_shelter.py`'s own run, or this script. Neither
`fix_shelter_bridge_mismatches.py` (Э5 follow-up, sqlite_migration.md
§XIV.2) nor `add_missing_shelter_items.py` touch it, so after running
either of those the stored costs are stale relative to the new bridges/
recipes until this runs. Safe to re-run any time — it's a pure
read-current-state-then-overwrite, no destructive side effects.

Lives in `scripts/`, not `src/` — one-shot maintenance, not part of the
coverage denominator (sqlite_migration.md §XI).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from stalbot.domain.clock import SystemClock
from stalbot.domain.shelter.cost import compute_costs
from stalbot.infrastructure.cache.db import CacheDb
from stalbot.infrastructure.cache.repositories.shelter import ShelterRepository

logger = logging.getLogger(__name__)


async def run(cache_db: CacheDb) -> tuple[int, int]:
    """Recompute and persist every shelter item's cost.

    Args:
        cache_db: An already-connected-or-not `CacheDb` for the live cache.

    Returns:
        `(items_computed, unresolved_count)`.
    """
    connection = await cache_db.connect()
    shelter = ShelterRepository(connection)

    items = await shelter.load_item_specs()
    recipes = await shelter.load_recipe_specs_for_current_levels()
    results = compute_costs(items, recipes)
    await shelter.save_costs(results, now=SystemClock().now())

    unresolved = sum(1 for r in results.values() if r.source == "unresolved")
    return len(results), unresolved


async def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, required=True)
    args = parser.parse_args(argv)

    cache_db = CacheDb(args.db_path)
    computed, unresolved = await run(cache_db)
    await cache_db.close()

    logger.info("Себестоимость пересчитана для %d предметов (unresolved: %d)", computed, unresolved)


if __name__ == "__main__":
    asyncio.run(main())
