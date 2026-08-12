"""SQLite-backed shelter (crafting cost) model.

sqlite_migration.md §IV.3, §V.2, Э5: `shelter_settings`, `professions`,
`shelter_items`, `recipes` + `recipe_ingredients` + `recipe_yields`, and
the materialized `shelter_cost`.

One repository, not five, because every table here exists to feed exactly
one computation (`domain.shelter.cost.compute_costs`) and nothing else yet
reads them independently — splitting now would just be five thin wrappers
around one shared connection with no separate callers.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction

import aiosqlite

from stalbot.domain.entities.shelter_item import ShelterItem
from stalbot.domain.shelter.cost import COST_CALCULATOR_VERSION, CostResult, ItemSpec, RecipeSpec
from stalbot.infrastructure.cache.db import transaction


@dataclass(frozen=True, slots=True)
class RecipeImport:
    """One recipe as read from `shelter_recipes.csv` (Э0), ready to insert."""

    output_name_norm: str
    profession_key: str
    source_sheet: str | None
    source_cell: str | None
    ingredients: tuple[tuple[str, Fraction], ...]
    """`(ingredient_name_norm, quantity)` pairs, in recipe order."""
    yields_by_level: Mapping[int, Fraction]
    """`{level: units_per_craft}` for levels 1-5 (0 where "Низкий уровень")."""


class ShelterRepository:
    """CRUD + the cost-calculator data plumbing for the shelter model."""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        """Wrap an already-open cache connection.

        Args:
            connection: Connection returned by `CacheDb.connect()`.
        """
        self._conn = connection

    # --- shelter_settings ---------------------------------------------------

    async def get_settings(self) -> dict[str, str]:
        """Return every `shelter_settings` row as a `key -> value` mapping."""
        cursor = await self._conn.execute("SELECT key, value FROM shelter_settings")
        return {row["key"]: row["value"] async for row in cursor}

    async def set_settings(self, values: Mapping[str, str]) -> None:
        """Insert or update several settings at once.

        Args:
            values: `key -> value` pairs to upsert.
        """
        if not values:
            return
        async with transaction(self._conn):
            await self._conn.executemany(
                """
                INSERT INTO shelter_settings (key, value) VALUES (?, ?)
                ON CONFLICT (key) DO UPDATE SET value = excluded.value
                """,
                list(values.items()),
            )

    # --- professions ----------------------------------------------------------

    async def get_professions(self) -> dict[str, int]:
        """Return every profession's current level, keyed by `professions.key`."""
        cursor = await self._conn.execute("SELECT key, level FROM professions")
        return {row["key"]: row["level"] async for row in cursor}

    async def set_professions(self, professions: Mapping[str, tuple[str, int]]) -> None:
        """Insert or update several professions at once.

        Args:
            professions: `key -> (name, level)` pairs to upsert.
        """
        if not professions:
            return
        async with transaction(self._conn):
            await self._conn.executemany(
                """
                INSERT INTO professions (key, name, level) VALUES (?, ?, ?)
                ON CONFLICT (key) DO UPDATE SET name = excluded.name, level = excluded.level
                """,
                [(key, name, level) for key, (name, level) in professions.items()],
            )

    async def set_profession_level(self, key: str, level: int) -> None:
        """Update one profession's level (`/shelter_level`, Э5's support table).

        Args:
            key: The profession's key.
            level: New level, 1-5.
        """
        async with transaction(self._conn):
            await self._conn.execute("UPDATE professions SET level = ? WHERE key = ?", (level, key))

    # --- shelter_items --------------------------------------------------------

    async def get_items_by_name(self) -> dict[str, ShelterItem]:
        """Return every shelter item, keyed by `name_norm`."""
        cursor = await self._conn.execute("SELECT * FROM shelter_items")
        return {row["name_norm"]: _row_to_item(row) async for row in cursor}

    async def all_items(self) -> Sequence[ShelterItem]:
        """Return every shelter item, ordered by id."""
        cursor = await self._conn.execute("SELECT * FROM shelter_items ORDER BY id")
        return [_row_to_item(row) async for row in cursor]

    async def insert_items(self, items: Sequence[ShelterItem]) -> dict[str, int]:
        """Insert several items, returning the assigned id per `name_norm`.

        Args:
            items: Items to insert. `item.id` is ignored.

        Returns:
            `name_norm -> assigned id`.
        """
        if not items:
            return {}
        async with transaction(self._conn):
            await self._conn.executemany(
                """
                INSERT INTO shelter_items
                    (name, name_norm, kind, market_kopeks, my_kopeks, vendor_kopeks, updated_at)
                VALUES (:name, :name_norm, :kind, :market_kopeks, :my_kopeks, :vendor_kopeks,
                        :updated_at)
                """,
                [_item_to_params(i) for i in items],
            )
        return await self._name_to_id_map()

    async def _name_to_id_map(self) -> dict[str, int]:
        cursor = await self._conn.execute("SELECT id, name_norm FROM shelter_items")
        return {row["name_norm"]: row["id"] async for row in cursor}

    async def set_my_price(self, item_id: int, my_kopeks: int | None) -> None:
        """Update one item's manual price override (`/shelter_price`).

        Args:
            item_id: The item to update.
            my_kopeks: New manual price, or `None` to clear it.
        """
        async with transaction(self._conn):
            await self._conn.execute(
                "UPDATE shelter_items SET my_kopeks = ? WHERE id = ?", (my_kopeks, item_id)
            )

    # --- recipes ----------------------------------------------------------

    async def insert_recipes(
        self, recipes: Sequence[RecipeImport], item_ids: Mapping[str, int]
    ) -> None:
        """Insert several recipes with their ingredients and per-level yields.

        Args:
            recipes: Recipes to insert (Э5's importer).
            item_ids: `name_norm -> shelter_items.id`, for resolving both
                the output and every ingredient.
        """
        if not recipes:
            return
        async with transaction(self._conn):
            for recipe in recipes:
                output_id = item_ids[recipe.output_name_norm]
                cursor = await self._conn.execute(
                    """
                    INSERT INTO recipes (output_item_id, profession_key, source_sheet, source_cell)
                    VALUES (?, ?, ?, ?)
                    """,
                    (output_id, recipe.profession_key, recipe.source_sheet, recipe.source_cell),
                )
                recipe_id = cursor.lastrowid
                assert recipe_id is not None  # noqa: S101 - lastrowid set right after INSERT
                if recipe.ingredients:
                    await self._conn.executemany(
                        """
                        INSERT INTO recipe_ingredients
                            (recipe_id, ingredient_item_id, quantity, position)
                        VALUES (?, ?, ?, ?)
                        """,
                        [
                            (recipe_id, item_ids[name_norm], str(qty), position)
                            for position, (name_norm, qty) in enumerate(recipe.ingredients, start=1)
                        ],
                    )
                await self._conn.executemany(
                    "INSERT INTO recipe_yields (recipe_id, level, units_per_craft) "
                    "VALUES (?, ?, ?)",
                    [
                        (recipe_id, level, str(units))
                        for level, units in recipe.yields_by_level.items()
                    ],
                )

    async def load_recipe_specs_for_current_levels(self) -> list[RecipeSpec]:
        """Build every `RecipeSpec` at each profession's *current* level.

        A recipe whose profession's current level has no
        `recipe_yields` row (shouldn't happen for real data, but not
        assumed) is treated as `units_per_craft = 0` — unavailable,
        same as an explicit zero.
        """
        professions = await self.get_professions()
        cursor = await self._conn.execute(
            "SELECT id, output_item_id, profession_key, source_sheet, source_cell FROM recipes"
        )
        recipe_rows = [dict(row) async for row in cursor]

        specs: list[RecipeSpec] = []
        for row in recipe_rows:
            level = professions.get(row["profession_key"], 1)
            yield_cursor = await self._conn.execute(
                "SELECT units_per_craft FROM recipe_yields WHERE recipe_id = ? AND level = ?",
                (row["id"], level),
            )
            yield_row = await yield_cursor.fetchone()
            units_per_craft = Fraction(yield_row["units_per_craft"]) if yield_row else Fraction(0)

            ingredient_cursor = await self._conn.execute(
                "SELECT ingredient_item_id, quantity FROM recipe_ingredients "
                "WHERE recipe_id = ? ORDER BY position",
                (row["id"],),
            )
            ingredient_pairs: list[tuple[int, Fraction]] = [
                (r["ingredient_item_id"], Fraction(r["quantity"])) async for r in ingredient_cursor
            ]
            ingredients = tuple(ingredient_pairs)
            specs.append(
                RecipeSpec(
                    recipe_id=row["id"],
                    output_item_id=row["output_item_id"],
                    profession_key=row["profession_key"],
                    units_per_craft=units_per_craft,
                    ingredients=ingredients,
                )
            )
        return specs

    async def load_item_specs(self) -> dict[int, ItemSpec]:
        """Build every `ItemSpec`, keyed by `shelter_items.id`."""
        cursor = await self._conn.execute(
            "SELECT id, kind, my_kopeks, market_kopeks FROM shelter_items"
        )
        return {
            row["id"]: ItemSpec(
                item_id=row["id"],
                kind=row["kind"],
                my_kopeks=row["my_kopeks"],
                market_kopeks=row["market_kopeks"],
            )
            async for row in cursor
        }

    # --- shelter_cost -----------------------------------------------------

    async def save_costs(self, results: Mapping[int, CostResult], *, now: datetime) -> None:
        """Replace every item's materialized cost in one transaction.

        Args:
            results: `shelter_item_id -> CostResult`, typically the whole
                output of `compute_costs()`.
            now: Timestamp to stamp every row with.
        """
        if not results:
            return
        async with transaction(self._conn):
            await self._conn.executemany(
                """
                INSERT INTO shelter_cost
                    (shelter_item_id, cost_kopeks, best_recipe_id, source, depth,
                     note, calculator_version, computed_at)
                VALUES (:item_id, :cost_kopeks, :best_recipe_id, :source, :depth,
                        :note, :calculator_version, :computed_at)
                ON CONFLICT (shelter_item_id) DO UPDATE SET
                    cost_kopeks        = excluded.cost_kopeks,
                    best_recipe_id     = excluded.best_recipe_id,
                    source             = excluded.source,
                    depth              = excluded.depth,
                    note               = excluded.note,
                    calculator_version = excluded.calculator_version,
                    computed_at        = excluded.computed_at
                """,
                [
                    {
                        "item_id": item_id,
                        "cost_kopeks": result.cost_kopeks,
                        "best_recipe_id": result.best_recipe_id,
                        "source": result.source,
                        "depth": result.depth,
                        "note": result.note,
                        "calculator_version": COST_CALCULATOR_VERSION,
                        "computed_at": now.isoformat(),
                    }
                    for item_id, result in results.items()
                ],
            )

    async def get_cost(self, item_id: int) -> CostResult | None:
        """Look up one item's materialized cost.

        Args:
            item_id: `shelter_items.id`.
        """
        cursor = await self._conn.execute(
            "SELECT * FROM shelter_cost WHERE shelter_item_id = ?", (item_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return CostResult(
            cost_kopeks=row["cost_kopeks"],
            best_recipe_id=row["best_recipe_id"],
            source=row["source"],
            depth=row["depth"],
            note=row["note"],
        )


def _item_to_params(item: ShelterItem) -> dict[str, object]:
    return {
        "name": item.name,
        "name_norm": item.name_norm,
        "kind": item.kind,
        "market_kopeks": item.market_kopeks,
        "my_kopeks": item.my_kopeks,
        "vendor_kopeks": item.vendor_kopeks,
        "updated_at": item.updated_at.isoformat() if item.updated_at is not None else None,
    }


def _row_to_item(row: aiosqlite.Row) -> ShelterItem:
    return ShelterItem(
        id=row["id"],
        name=row["name"],
        name_norm=row["name_norm"],
        kind=row["kind"],
        market_kopeks=row["market_kopeks"],
        my_kopeks=row["my_kopeks"],
        vendor_kopeks=row["vendor_kopeks"],
        updated_at=(
            datetime.fromisoformat(row["updated_at"]) if row["updated_at"] is not None else None
        ),
    )
