"""Direct unit tests for `domain.shelter.cost.compute_costs` (sqlite_migration.md
§V.2, Э5). Synthetic graphs only — the real 421-item/370-recipe snapshot is
exercised in `tests/unit/scripts/test_import_shelter.py`.
"""

from fractions import Fraction

from stalbot.domain.shelter.cost import ItemSpec, RecipeSpec, compute_costs


def test_my_price_short_circuits_even_with_a_recipe_present() -> None:
    """§V.2 rule 1: a manual price wins and stops recursion — the recipe
    is never evaluated, matching the sheet's `H = G ?? F`."""
    items = {
        1: ItemSpec(1, "craftable", my_kopeks=500, market_kopeks=None),
        2: ItemSpec(2, "component", my_kopeks=None, market_kopeks=999_999),
    }
    recipes = [RecipeSpec(10, 1, "cooking", Fraction(1), ((2, Fraction(1)),))]

    result = compute_costs(items, recipes)[1]

    assert result.source == "my_price"
    assert result.cost_kopeks == 500
    assert result.best_recipe_id is None


def test_no_recipe_falls_back_to_market() -> None:
    items = {1: ItemSpec(1, "component", my_kopeks=None, market_kopeks=250)}

    result = compute_costs(items, [])[1]

    assert result.source == "market"
    assert result.cost_kopeks == 250


def test_no_recipe_and_no_price_is_unresolved() -> None:
    items = {1: ItemSpec(1, "component", my_kopeks=None, market_kopeks=None)}

    result = compute_costs(items, [])[1]

    assert result.source == "unresolved"
    assert result.cost_kopeks is None
    assert result.note is not None


def test_crafted_cost_is_ceil_of_ingredient_sum_over_yield() -> None:
    items = {
        1: ItemSpec(1, "craftable", None, None),
        2: ItemSpec(2, "component", None, 100),
        3: ItemSpec(3, "component", None, 30),
    }
    # 2 * 100 + 3 * 30 = 290, / 4 units = 72.5 -> ceil 73
    recipes = [
        RecipeSpec(10, 1, "cooking", Fraction(4), ((2, Fraction(2)), (3, Fraction(3)))),
    ]

    result = compute_costs(items, recipes)[1]

    assert result.source == "crafted"
    assert result.cost_kopeks == 73
    assert result.best_recipe_id == 10


def test_compute_costs_multi_recipe_picks_the_cheaper_one() -> None:
    """§V.2 rule 3 / Level F (§VI.2): with two available recipes, the
    cheaper one wins, and `best_recipe_id` names it."""
    items = {
        1: ItemSpec(1, "craftable", None, None),
        2: ItemSpec(2, "component", None, 100),  # cheap path ingredient
        3: ItemSpec(3, "component", None, 10_000),  # expensive path ingredient
    }
    cheap_recipe = RecipeSpec(10, 1, "cooking", Fraction(1), ((2, Fraction(1)),))  # cost 100
    expensive_recipe = RecipeSpec(11, 1, "cooking", Fraction(1), ((3, Fraction(1)),))  # cost 10000

    result = compute_costs(items, [expensive_recipe, cheap_recipe])[1]

    assert result.source == "crafted"
    assert result.cost_kopeks == 100
    assert result.best_recipe_id == 10


def test_recipe_with_zero_yield_is_treated_as_unavailable() -> None:
    """ "Низкий уровень" (§II.2): a 0 `units_per_craft` recipe is simply not
    offered as a candidate — the caller is expected to have already
    filtered to `units_per_craft > 0` before building `RecipeSpec`s that
    represent "available", but `compute_costs` re-guards this itself too."""
    items = {
        1: ItemSpec(1, "craftable", None, None),
        2: ItemSpec(2, "component", None, 100),
    }
    unavailable_recipe = RecipeSpec(10, 1, "armor", Fraction(0), ((2, Fraction(1)),))

    result = compute_costs(items, [unavailable_recipe])[1]

    assert result.source == "unresolved"


def test_self_referencing_recipe_does_not_recurse_forever() -> None:
    """The exact §II.2 "Антитоксин" shape: a recipe listing its own output
    among its ingredients, with no `my_price`/`market_kopeks` escape hatch
    and no alternate recipe — must resolve to `unresolved` with a `cycle`
    note, never a `RecursionError`."""
    items = {1: ItemSpec(1, "craftable", my_kopeks=None, market_kopeks=None)}
    recipes = [RecipeSpec(10, 1, "medicine", Fraction(1), ((1, Fraction(6)),))]

    result = compute_costs(items, recipes)[1]

    assert result.source == "unresolved"
    assert result.cost_kopeks is None
    assert result.note is not None
    assert "cycle" in result.note


def test_cycle_falls_back_to_market_if_available() -> None:
    """A cyclic recipe fails, but a market price still rescues the item —
    `source == 'market'`, not `unresolved`, and the cycle is noted anyway
    for diagnostics."""
    items = {1: ItemSpec(1, "craftable", my_kopeks=None, market_kopeks=777)}
    recipes = [RecipeSpec(10, 1, "medicine", Fraction(1), ((1, Fraction(6)),))]

    result = compute_costs(items, recipes)[1]

    assert result.source == "market"
    assert result.cost_kopeks == 777
    assert result.note is not None and "cycle" in result.note


def test_cycle_in_one_recipe_does_not_block_a_working_alternate_recipe() -> None:
    """The real "Антитоксин" shape once its `my_price` override is
    ignored (hypothetically): one recipe cycles back to itself, a second,
    independent recipe succeeds — the item resolves via the working one."""
    items = {
        1: ItemSpec(1, "craftable", None, None),  # Антитоксин
        2: ItemSpec(2, "component", None, 100),  # a real base ingredient
    }
    cyclic_recipe = RecipeSpec(10, 1, "medicine", Fraction(1), ((1, Fraction(6)),))
    working_recipe = RecipeSpec(11, 1, "materials", Fraction(2), ((2, Fraction(1)),))  # 100/2=50

    result = compute_costs(items, [cyclic_recipe, working_recipe])[1]

    assert result.source == "crafted"
    assert result.cost_kopeks == 50
    assert result.best_recipe_id == 11


def test_transitive_ingredient_is_resolved_recursively() -> None:
    """A -> B -> C, no shortcuts: cost(A) depends on cost(B), which itself
    depends on cost(C) — proves multi-level recursion, not just one hop."""
    items = {
        1: ItemSpec(1, "craftable", None, None),  # A
        2: ItemSpec(2, "craftable", None, None),  # B
        3: ItemSpec(3, "component", None, 40),  # C
    }
    recipe_b_from_c = RecipeSpec(20, 2, "cooking", Fraction(1), ((3, Fraction(2)),))  # B = 80
    recipe_a_from_b = RecipeSpec(10, 1, "cooking", Fraction(1), ((2, Fraction(1)),))  # A = 80

    results = compute_costs(items, [recipe_a_from_b, recipe_b_from_c])

    assert results[2].cost_kopeks == 80
    assert results[1].cost_kopeks == 80


def test_unknown_ingredient_id_resolves_unresolved_not_a_crash() -> None:
    """A recipe referencing an item id that isn't in `items` (a data
    integrity gap, not something that should happen with a real import)
    must degrade to `unresolved`, not raise `KeyError`."""
    items = {1: ItemSpec(1, "craftable", None, None)}
    recipes = [RecipeSpec(10, 1, "cooking", Fraction(1), ((999, Fraction(1)),))]

    result = compute_costs(items, recipes)[1]

    assert result.source == "unresolved"


def test_fractional_quantities_and_yields_use_exact_fraction_math() -> None:
    """ "Ед. за крафт" is often fractional (4.5/6.25/14.5, §II.2) — proves no
    float drift by using values that are exact in `Fraction` but would
    round visibly if handled as `float` at any intermediate step."""
    items = {
        1: ItemSpec(1, "craftable", None, None),
        2: ItemSpec(2, "component", None, 1),  # 1 kopek
    }
    # 3 * 1 = 3, / (1/3) units = 9 exactly — a naive float division of
    # awkward fractions could easily drift off an integer here.
    recipes = [RecipeSpec(10, 1, "cooking", Fraction(1, 3), ((2, Fraction(3)),))]

    result = compute_costs(items, recipes)[1]

    assert result.cost_kopeks == 9
