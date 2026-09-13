"""`RecipeRecord` — one stored recipe with its ingredients and yields.

`domain.shelter.cost.RecipeSpec` is the *calculator's* view: one recipe
already resolved to the profession's current level, with a single
`units_per_craft`. This is the whole stored recipe, all five levels — what
`/recipe show` displays and `/recipe edit` rewrites (заявка 13.09.2026 п.3).
"""

from dataclasses import dataclass, field
from fractions import Fraction


@dataclass(frozen=True, slots=True)
class RecipeRecord:
    """One `recipes` row, joined with its ingredients and per-level yields."""

    id: int | None
    """`None` for a not-yet-persisted recipe."""
    output_item_id: int
    profession_key: str
    ingredients: tuple[tuple[int, Fraction], ...] = ()
    """`(shelter_items.id, quantity)` pairs, in recipe order. Энергия is one
    of these like any other ingredient — it is a real `shelter_items` row."""
    yields: dict[int, Fraction] = field(default_factory=dict)
    """`{level: units per craft}` for levels 1-5. Zero means the recipe is
    not available at that level (the sheet's «Низкий уровень», §II.2)."""
    source_sheet: str | None = None
    source_cell: str | None = None
    """Where the importer found it. `None` for a recipe added by hand."""
