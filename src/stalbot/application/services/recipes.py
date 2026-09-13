"""Recipe CRUD over the shelter model (заявка 13.09.2026 п.3).

Backs the `/recipe` group. Every table it touches — `recipes`,
`recipe_ingredients`, `recipe_yields`, `professions`, `shelter_items` — has
existed since the shelter import; what was missing was any way to edit them
without opening a CSV and re-running a script.

Two things this service is careful about:

* **Names, not ids.** The owner types ingredient names, so every write
  resolves them against `shelter_items` and refuses the whole recipe if any
  name is unknown. Half a recipe still computes a cost — a quietly wrong one.
* **Costs stay current.** Every write ends with a `shelter_cost` recompute,
  because the next thing the owner does is run `/cost` and expect it to
  reflect what they just typed.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction

from stalbot.application.ports.clock import Clock
from stalbot.application.services.shelter_cost import ShelterCostService
from stalbot.domain.entities.recipe import RecipeRecord
from stalbot.domain.entities.shelter_item import ShelterItem
from stalbot.domain.errors import DomainError, ItemNotFoundError
from stalbot.domain.shelter.cost import CostResult
from stalbot.domain.shelter.recipe_text import MAX_LEVEL, MIN_LEVEL, fill_missing_levels
from stalbot.infrastructure.cache.repositories.items import normalize_item_name
from stalbot.infrastructure.cache.repositories.shelter import ShelterRepository

#: `shelter_items.kind` for Энергия — the importer marks it this way, and it
#: is the only virtual item. Matched on `kind` rather than the name so a
#: rename can't quietly turn energy into an ordinary ingredient.
_VIRTUAL_KIND = "virtual"


class UnknownIngredientsError(DomainError):
    """One or more typed ingredient names match no shelter item."""

    def __init__(self, names: Sequence[str]) -> None:
        """Name every unresolved ingredient, so the owner can fix them in one pass.

        Args:
            names: The ingredient names that matched nothing.
        """
        self.names = tuple(names)
        listed = ", ".join(f"«{name}»" for name in self.names)
        super().__init__(f"нет таких предметов убежки: {listed}")


class RecipeNotFoundError(DomainError):
    """No recipe with that id."""


class UnknownProfessionError(DomainError):
    """No profession with that key."""


class EmptyRecipeError(DomainError):
    """A recipe with no ingredients, or available at no level, is not a recipe."""


class DuplicateShelterItemError(DomainError):
    """A shelter item with that name already exists."""


@dataclass(frozen=True, slots=True)
class IngredientView:
    """One ingredient of a recipe, priced."""

    item_id: int
    name: str
    quantity: Fraction
    unit_cost_kopeks: int | None
    is_energy: bool

    @property
    def total_kopeks(self) -> int | None:
        """What this ingredient contributes to one craft, or `None` if unpriced."""
        if self.unit_cost_kopeks is None:
            return None
        return int(self.quantity * self.unit_cost_kopeks)


@dataclass(frozen=True, slots=True)
class RecipeView:
    """One recipe, resolved for display."""

    recipe_id: int
    output_item_id: int
    output_name: str
    profession_key: str
    profession_name: str
    profession_level: int
    ingredients: tuple[IngredientView, ...]
    yields: dict[int, Fraction]
    source_sheet: str | None
    source_cell: str | None

    @property
    def energy(self) -> IngredientView | None:
        """The energy line, pulled out — the owner asks for it by name."""
        return next((line for line in self.ingredients if line.is_energy), None)

    @property
    def materials(self) -> tuple[IngredientView, ...]:
        """Everything that is not energy."""
        return tuple(line for line in self.ingredients if not line.is_energy)

    @property
    def units_at_current_level(self) -> Fraction:
        """Units per craft at the profession's current level. Zero means locked."""
        return self.yields.get(self.profession_level, Fraction(0))

    @property
    def is_available_now(self) -> bool:
        """Whether this recipe can be crafted at the current profession level."""
        return self.units_at_current_level > 0

    @property
    def batch_cost_kopeks(self) -> int | None:
        """Cost of one craft — every ingredient summed. `None` if any is unpriced."""
        totals = [line.total_kopeks for line in self.ingredients]
        if any(total is None for total in totals):
            return None
        return sum(total for total in totals if total is not None)

    @property
    def unit_cost_kopeks(self) -> int | None:
        """Cost per produced unit at the current level, rounded up like the calculator.

        `None` when the recipe is locked at this level (dividing by a zero
        yield) or an ingredient has no cost.
        """
        batch = self.batch_cost_kopeks
        units = self.units_at_current_level
        if batch is None or units <= 0:
            return None
        exact = Fraction(batch) / units
        whole, remainder = divmod(exact.numerator, exact.denominator)
        return whole + 1 if remainder else whole


class RecipeService:
    """Lists, shows, creates, edits and deletes shelter recipes."""

    def __init__(
        self, shelter: ShelterRepository, costs: ShelterCostService, *, clock: Clock
    ) -> None:
        """Wire the service to its collaborators.

        Args:
            shelter: Cache repository for the whole shelter model.
            costs: Recomputes `shelter_cost` after every write, and prices
                ingredients for display.
            clock: Time source for `shelter_items.updated_at`.
        """
        self._shelter = shelter
        self._costs = costs
        self._clock = clock

    # --- reads -------------------------------------------------------------

    async def list_recipes(self, *, profession_key: str | None = None) -> list[RecipeView]:
        """Every recipe, optionally narrowed to one profession.

        Args:
            profession_key: `professions.key`, or `None` for all.

        Raises:
            UnknownProfessionError: No profession with that key.
        """
        if (
            profession_key is not None
            and profession_key not in await self._shelter.get_professions()
        ):
            raise UnknownProfessionError(f"нет профессии «{profession_key}»")
        records = await self._shelter.all_recipes(profession_key=profession_key)
        return await self._to_views(records)

    async def show(self, output_item_id: int) -> list[RecipeView]:
        """Every recipe producing one item — there may be several.

        Args:
            output_item_id: `shelter_items.id` of the crafted item.

        Raises:
            ItemNotFoundError: No such shelter item.
        """
        if await self._shelter.get_item(output_item_id) is None:
            raise ItemNotFoundError(str(output_item_id))
        return await self._to_views(await self._shelter.recipes_for_output(output_item_id))

    async def get(self, recipe_id: int) -> RecipeView:
        """One recipe by id.

        Args:
            recipe_id: `recipes.id`.

        Raises:
            RecipeNotFoundError: No such recipe.
        """
        record = await self._shelter.get_recipe(recipe_id)
        if record is None:
            raise RecipeNotFoundError(f"рецепт #{recipe_id} не найден")
        views = await self._to_views([record])
        return views[0]

    async def professions(self) -> dict[str, tuple[str, int]]:
        """`key -> (display name, current level)` for every profession."""
        return await self._profession_map()

    # --- writes ------------------------------------------------------------

    async def create(
        self,
        output_item_id: int,
        profession_key: str,
        ingredients: Sequence[tuple[str, Fraction]],
        yields: dict[int, Fraction],
    ) -> RecipeView:
        """Add a recipe.

        Args:
            output_item_id: What the recipe produces.
            profession_key: Which profession crafts it.
            ingredients: `(typed name, quantity)` pairs.
            yields: `{level: units per craft}`; missing levels become zero.

        Raises:
            ItemNotFoundError: The output item does not exist.
            UnknownProfessionError: No profession with that key.
            UnknownIngredientsError: An ingredient name matched nothing.
            EmptyRecipeError: No ingredients, or zero yield at every level.
        """
        await self._require_item(output_item_id)
        await self._require_profession(profession_key)
        resolved = await self._resolve_ingredients(ingredients)
        filled = self._require_usable(resolved, yields)

        recipe_id = await self._shelter.create_recipe(
            RecipeRecord(
                id=None,
                output_item_id=output_item_id,
                profession_key=profession_key,
                ingredients=resolved,
                yields=filled,
            )
        )
        await self._costs.recompute()
        return await self.get(recipe_id)

    async def update(
        self,
        recipe_id: int,
        *,
        profession_key: str | None = None,
        ingredients: Sequence[tuple[str, Fraction]] | None = None,
        yields: dict[int, Fraction] | None = None,
    ) -> RecipeView:
        """Rewrite a recipe. Anything left `None` keeps its current value.

        Args:
            recipe_id: `recipes.id`.
            profession_key: New profession, or `None` to keep it.
            ingredients: New `(typed name, quantity)` pairs, or `None` to keep them.
            yields: New per-level yields, or `None` to keep them.

        Raises:
            RecipeNotFoundError: No such recipe.
            UnknownProfessionError: No profession with that key.
            UnknownIngredientsError: An ingredient name matched nothing.
            EmptyRecipeError: The result would have no ingredients or no level.
        """
        current = await self._shelter.get_recipe(recipe_id)
        if current is None:
            raise RecipeNotFoundError(f"рецепт #{recipe_id} не найден")

        if profession_key is not None:
            await self._require_profession(profession_key)
        resolved = (
            current.ingredients
            if ingredients is None
            else await self._resolve_ingredients(ingredients)
        )
        filled = self._require_usable(resolved, current.yields if yields is None else yields)

        await self._shelter.update_recipe(
            RecipeRecord(
                id=recipe_id,
                output_item_id=current.output_item_id,
                profession_key=profession_key or current.profession_key,
                ingredients=resolved,
                yields=filled,
                source_sheet=current.source_sheet,
                source_cell=current.source_cell,
            )
        )
        await self._costs.recompute()
        return await self.get(recipe_id)

    async def delete(self, recipe_id: int) -> RecipeView:
        """Delete a recipe, returning what was removed.

        Args:
            recipe_id: `recipes.id`.

        Raises:
            RecipeNotFoundError: No such recipe.
        """
        view = await self.get(recipe_id)
        await self._shelter.delete_recipe(recipe_id)
        await self._costs.recompute()
        return view

    async def add_item(
        self,
        name: str,
        kind: str,
        *,
        my_kopeks: int | None = None,
        market_kopeks: int | None = None,
    ) -> ShelterItem:
        """Add a shelter item, so a recipe can be written against it.

        Without this, a new recipe could only ever be made of ingredients
        the original import happened to include — which is the code-editing
        the recipe commands exist to end.

        Args:
            name: Display name, as the owner writes it.
            kind: `component` | `craftable` | `virtual`.
            my_kopeks: Manual price override. Wins over everything else in
                the calculator and stops the recursion (§V.2).
            market_kopeks: Market price, used when there is no recipe.

        Raises:
            DuplicateShelterItemError: The name is already taken.
        """
        name_norm = normalize_item_name(name)
        if name_norm in await self._shelter.get_items_by_name():
            raise DuplicateShelterItemError(f"предмет «{name}» уже есть в убежке")
        now = self._clock.now()
        item_id = await self._shelter.insert_item(
            ShelterItem(
                id=None,
                name=name,
                name_norm=name_norm,
                kind=kind,
                market_kopeks=market_kopeks,
                my_kopeks=my_kopeks,
                vendor_kopeks=None,
                updated_at=now,
            ),
            now=now,
        )
        await self._costs.recompute()
        stored = await self._shelter.get_item(item_id)
        assert stored is not None  # noqa: S101 - just inserted
        return stored

    async def set_item_prices(
        self,
        item_id: int,
        *,
        my_kopeks: int | None = None,
        market_kopeks: int | None = None,
        clear_my_price: bool = False,
    ) -> ShelterItem:
        """Change an item's prices and recompute everything they feed.

        Args:
            item_id: `shelter_items.id`.
            my_kopeks: New manual price, or `None` to leave it alone.
            market_kopeks: New market price, or `None` to leave it alone.
            clear_my_price: Drop the manual override, so the item falls back
                to its recipe or market price.

        Raises:
            ItemNotFoundError: No such shelter item.
        """
        item = await self._require_item(item_id)
        now = self._clock.now()
        await self._shelter.update_item(
            ShelterItem(
                id=item.id,
                name=item.name,
                name_norm=item.name_norm,
                kind=item.kind,
                market_kopeks=item.market_kopeks if market_kopeks is None else market_kopeks,
                my_kopeks=None
                if clear_my_price
                else (item.my_kopeks if my_kopeks is None else my_kopeks),
                vendor_kopeks=item.vendor_kopeks,
                updated_at=now,
            ),
            now=now,
        )
        await self._costs.recompute()
        updated = await self._shelter.get_item(item_id)
        assert updated is not None  # noqa: S101 - fetched a moment ago
        return updated

    async def set_profession_level(self, profession_key: str, level: int) -> tuple[str, int]:
        """Change a profession's level and recompute costs.

        The level decides which `recipe_yields` row the calculator reads, so
        it moves the cost of everything that profession crafts.

        Args:
            profession_key: `professions.key`.
            level: New level, 1-5.

        Returns:
            `(display name, level)`.

        Raises:
            UnknownProfessionError: No profession with that key.
            ValueError: Level outside 1-5.
        """
        name, _current = await self._require_profession(profession_key)
        if not MIN_LEVEL <= level <= MAX_LEVEL:
            raise ValueError(f"уровень должен быть от {MIN_LEVEL} до {MAX_LEVEL}")
        await self._shelter.set_profession_level(profession_key, level)
        await self._costs.recompute()
        return name, level

    # --- internals ---------------------------------------------------------

    async def _require_item(self, item_id: int) -> ShelterItem:
        item = await self._shelter.get_item(item_id)
        if item is None:
            raise ItemNotFoundError(str(item_id))
        return item

    async def _require_profession(self, key: str) -> tuple[str, int]:
        professions = await self._profession_map()
        if key not in professions:
            raise UnknownProfessionError(f"нет профессии «{key}»")
        return professions[key]

    async def _profession_map(self) -> dict[str, tuple[str, int]]:
        return await self._shelter.get_profession_rows()

    async def _resolve_ingredients(
        self, ingredients: Sequence[tuple[str, Fraction]]
    ) -> tuple[tuple[int, Fraction], ...]:
        """Turn typed names into item ids, refusing the lot if any is unknown."""
        by_name = await self._shelter.get_items_by_name()
        resolved: list[tuple[int, Fraction]] = []
        unknown: list[str] = []
        for name, quantity in ingredients:
            item = by_name.get(normalize_item_name(name))
            if item is None or item.id is None:
                unknown.append(name)
                continue
            resolved.append((item.id, quantity))
        if unknown:
            raise UnknownIngredientsError(unknown)
        return tuple(resolved)

    def _require_usable(
        self, ingredients: tuple[tuple[int, Fraction], ...], yields: dict[int, Fraction]
    ) -> dict[int, Fraction]:
        """Reject a recipe that could never produce anything, and fill in the zeros."""
        if not ingredients:
            raise EmptyRecipeError("в рецепте нет ни одного ингредиента")
        filled = fill_missing_levels(yields)
        if all(units <= 0 for units in filled.values()):
            raise EmptyRecipeError("рецепт недоступен ни на одном уровне — нечего считать")
        return filled

    async def _to_views(self, records: Sequence[RecipeRecord]) -> list[RecipeView]:
        if not records:
            return []
        items = {item.id: item for item in await self._shelter.all_items() if item.id is not None}
        professions = await self._profession_map()
        costs = await self._costs.current_costs()
        return [self._to_view(record, items, professions, costs) for record in records]

    def _to_view(
        self,
        record: RecipeRecord,
        items: dict[int, ShelterItem],
        professions: dict[str, tuple[str, int]],
        costs: dict[int, CostResult],
    ) -> RecipeView:
        assert record.id is not None  # noqa: S101 - a fetched recipe always has an id
        output = items.get(record.output_item_id)
        name, level = professions.get(record.profession_key, (record.profession_key, 1))
        lines: list[IngredientView] = []
        for item_id, quantity in record.ingredients:
            item = items.get(item_id)
            result = costs.get(item_id)
            lines.append(
                IngredientView(
                    item_id=item_id,
                    name=item.name if item is not None else f"#{item_id}",
                    quantity=quantity,
                    unit_cost_kopeks=result.cost_kopeks if result is not None else None,
                    is_energy=item is not None and item.kind == _VIRTUAL_KIND,
                )
            )
        return RecipeView(
            recipe_id=record.id,
            output_item_id=record.output_item_id,
            output_name=output.name if output is not None else f"#{record.output_item_id}",
            profession_key=record.profession_key,
            profession_name=name,
            profession_level=level,
            ingredients=tuple(lines),
            yields=dict(record.yields),
            source_sheet=record.source_sheet,
            source_cell=record.source_cell,
        )
