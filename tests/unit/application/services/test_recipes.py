"""Tests for `application.services.recipes` (заявка 13.09.2026 п.3).

Real SQLite repositories throughout — the recipe tables have three-way
foreign keys and a cost recompute hanging off every write, and a mocked
repository would prove none of it.
"""

from datetime import UTC, datetime
from fractions import Fraction

import aiosqlite
import pytest

from stalbot.application.services.recipes import (
    DuplicateShelterItemError,
    EmptyRecipeError,
    RecipeNotFoundError,
    RecipeService,
    UnknownIngredientsError,
    UnknownProfessionError,
)
from stalbot.application.services.shelter_cost import ShelterCostService
from stalbot.domain.entities.shelter_item import ShelterItem
from stalbot.domain.errors import ItemNotFoundError
from stalbot.infrastructure.cache.repositories.shelter import ShelterRepository
from tests.support.fake_clock import FakeClock

_NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)

#: The only `kind='virtual'` item — Энергия, priced per unit and consumed
#: like any other ingredient.
_ENERGY = "Энергия"


def _item(name: str, *, kind: str = "component", my_kopeks: int | None = None) -> ShelterItem:
    return ShelterItem(
        id=None,
        name=name,
        name_norm=name.casefold(),
        kind=kind,
        market_kopeks=None,
        my_kopeks=my_kopeks,
        vendor_kopeks=None,
        updated_at=_NOW,
    )


async def _seed(
    connection: aiosqlite.Connection,
) -> tuple[RecipeService, ShelterRepository, dict[str, int]]:
    shelter = ShelterRepository(connection)
    await shelter.set_professions({"cooking": ("Кулинария", 1), "medicine": ("Медицина", 3)})
    ids = await shelter.insert_items(
        [
            _item("Мякоть", my_kopeks=1000),
            _item("Спирт", my_kopeks=500),
            _item(_ENERGY, kind="virtual", my_kopeks=1),
            _item("Настойка", kind="craftable"),
        ]
    )
    costs = ShelterCostService(shelter, clock=FakeClock(_NOW))
    return RecipeService(shelter, costs, clock=FakeClock(_NOW)), shelter, ids


# -- create -----------------------------------------------------------------


async def test_create_stores_ingredients_and_yields(connection: aiosqlite.Connection) -> None:
    service, shelter, ids = await _seed(connection)

    view = await service.create(
        ids["настойка"],
        "cooking",
        [("Мякоть", Fraction(2)), (_ENERGY, Fraction(900))],
        {1: Fraction(4)},
    )

    stored = await shelter.get_recipe(view.recipe_id)
    assert stored is not None
    assert stored.ingredients == ((ids["мякоть"], Fraction(2)), (ids["энергия"], Fraction(900)))
    assert stored.yields[1] == Fraction(4)


async def test_create_writes_out_the_zero_levels(connection: aiosqlite.Connection) -> None:
    """A missing level means «недоступно» — stored explicitly, not left to inference."""
    service, shelter, ids = await _seed(connection)

    view = await service.create(
        ids["настойка"], "cooking", [("Мякоть", Fraction(1))], {3: Fraction(5)}
    )

    stored = await shelter.get_recipe(view.recipe_id)
    assert stored is not None
    assert stored.yields == {
        1: Fraction(0),
        2: Fraction(0),
        3: Fraction(5),
        4: Fraction(0),
        5: Fraction(0),
    }


async def test_create_resolves_names_case_insensitively(
    connection: aiosqlite.Connection,
) -> None:
    service, _shelter, ids = await _seed(connection)

    view = await service.create(
        ids["настойка"], "cooking", [("мЯкОтЬ", Fraction(1))], {1: Fraction(1)}
    )

    assert view.ingredients[0].name == "Мякоть"


async def test_create_refuses_the_whole_recipe_on_one_unknown_ingredient(
    connection: aiosqlite.Connection,
) -> None:
    """Half a recipe still computes a cost — a quietly wrong one."""
    service, shelter, ids = await _seed(connection)

    with pytest.raises(UnknownIngredientsError) as excinfo:
        await service.create(
            ids["настойка"],
            "cooking",
            [("Мякоть", Fraction(1)), ("Выдумка", Fraction(2))],
            {1: Fraction(1)},
        )

    assert excinfo.value.names == ("Выдумка",)
    assert await shelter.all_recipes() == []


async def test_create_names_every_unknown_ingredient_at_once(
    connection: aiosqlite.Connection,
) -> None:
    service, _shelter, ids = await _seed(connection)

    with pytest.raises(UnknownIngredientsError) as excinfo:
        await service.create(
            ids["настойка"],
            "cooking",
            [("Выдумка", Fraction(1)), ("Небылица", Fraction(1))],
            {1: Fraction(1)},
        )

    assert excinfo.value.names == ("Выдумка", "Небылица")


async def test_create_refuses_an_unknown_profession(connection: aiosqlite.Connection) -> None:
    service, _shelter, ids = await _seed(connection)

    with pytest.raises(UnknownProfessionError):
        await service.create(
            ids["настойка"], "alchemy", [("Мякоть", Fraction(1))], {1: Fraction(1)}
        )


async def test_create_refuses_an_unknown_output_item(connection: aiosqlite.Connection) -> None:
    service, _shelter, _ids = await _seed(connection)

    with pytest.raises(ItemNotFoundError):
        await service.create(999, "cooking", [("Мякоть", Fraction(1))], {1: Fraction(1)})


async def test_create_refuses_a_recipe_with_no_ingredients(
    connection: aiosqlite.Connection,
) -> None:
    service, _shelter, ids = await _seed(connection)

    with pytest.raises(EmptyRecipeError):
        await service.create(ids["настойка"], "cooking", [], {1: Fraction(1)})


async def test_create_refuses_a_recipe_locked_at_every_level(
    connection: aiosqlite.Connection,
) -> None:
    """Zero everywhere is not a recipe — nothing could ever come out of it."""
    service, _shelter, ids = await _seed(connection)

    with pytest.raises(EmptyRecipeError):
        await service.create(
            ids["настойка"], "cooking", [("Мякоть", Fraction(1))], {1: Fraction(0)}
        )


async def test_create_recomputes_the_materialized_cost(
    connection: aiosqlite.Connection,
) -> None:
    """`/cost` must agree with what was just typed, without a manual script run."""
    service, shelter, ids = await _seed(connection)

    await service.create(ids["настойка"], "cooking", [("Мякоть", Fraction(2))], {1: Fraction(1)})

    stored = await shelter.get_cost(ids["настойка"])
    assert stored is not None
    assert stored.cost_kopeks == 2000
    assert stored.source == "crafted"


# -- read -------------------------------------------------------------------


async def test_show_separates_energy_from_the_materials(
    connection: aiosqlite.Connection,
) -> None:
    """«Сколько энергии тратится» is the owner's own wording — it gets its own line."""
    service, _shelter, ids = await _seed(connection)
    await service.create(
        ids["настойка"],
        "cooking",
        [("Мякоть", Fraction(2)), (_ENERGY, Fraction(900))],
        {1: Fraction(4)},
    )

    view = (await service.show(ids["настойка"]))[0]

    assert [line.name for line in view.materials] == ["Мякоть"]
    assert view.energy is not None
    assert view.energy.quantity == Fraction(900)


async def test_show_prices_the_whole_craft_and_one_unit(
    connection: aiosqlite.Connection,
) -> None:
    service, _shelter, ids = await _seed(connection)
    await service.create(
        ids["настойка"],
        "cooking",
        [("Мякоть", Fraction(2)), (_ENERGY, Fraction(100))],
        {1: Fraction(4)},
    )

    view = (await service.show(ids["настойка"]))[0]

    assert view.batch_cost_kopeks == 2 * 1000 + 100 * 1
    assert view.unit_cost_kopeks == 525  # 2100 / 4


async def test_unit_cost_rounds_up_like_the_calculator(
    connection: aiosqlite.Connection,
) -> None:
    service, _shelter, ids = await _seed(connection)
    await service.create(ids["настойка"], "cooking", [("Мякоть", Fraction(1))], {1: Fraction(3)})

    view = (await service.show(ids["настойка"]))[0]

    assert view.unit_cost_kopeks == 334  # ceil(1000 / 3)


async def test_a_recipe_locked_at_the_current_level_reports_no_unit_cost(
    connection: aiosqlite.Connection,
) -> None:
    service, _shelter, ids = await _seed(connection)
    # cooking sits at level 1, and this recipe only unlocks at 3.
    await service.create(ids["настойка"], "cooking", [("Мякоть", Fraction(1))], {3: Fraction(5)})

    view = (await service.show(ids["настойка"]))[0]

    assert view.is_available_now is False
    assert view.unit_cost_kopeks is None


async def test_show_returns_every_recipe_for_one_item(
    connection: aiosqlite.Connection,
) -> None:
    """27 items have more than one recipe (§II.2)."""
    service, _shelter, ids = await _seed(connection)
    await service.create(ids["настойка"], "cooking", [("Мякоть", Fraction(1))], {1: Fraction(1)})
    await service.create(ids["настойка"], "cooking", [("Спирт", Fraction(1))], {1: Fraction(1)})

    assert len(await service.show(ids["настойка"])) == 2


async def test_show_refuses_an_unknown_item(connection: aiosqlite.Connection) -> None:
    service, _shelter, _ids = await _seed(connection)

    with pytest.raises(ItemNotFoundError):
        await service.show(999)


async def test_list_filters_by_profession(connection: aiosqlite.Connection) -> None:
    service, _shelter, ids = await _seed(connection)
    await service.create(ids["настойка"], "cooking", [("Мякоть", Fraction(1))], {1: Fraction(1)})
    await service.create(ids["настойка"], "medicine", [("Спирт", Fraction(1))], {3: Fraction(1)})

    assert len(await service.list_recipes(profession_key="cooking")) == 1
    assert len(await service.list_recipes()) == 2


async def test_list_refuses_an_unknown_profession(connection: aiosqlite.Connection) -> None:
    service, _shelter, _ids = await _seed(connection)

    with pytest.raises(UnknownProfessionError):
        await service.list_recipes(profession_key="alchemy")


async def test_view_names_the_profession_the_way_the_owner_reads_it(
    connection: aiosqlite.Connection,
) -> None:
    service, _shelter, ids = await _seed(connection)
    await service.create(ids["настойка"], "cooking", [("Мякоть", Fraction(1))], {1: Fraction(1)})

    view = (await service.show(ids["настойка"]))[0]

    assert view.profession_name == "Кулинария"
    assert view.profession_level == 1


# -- update / delete --------------------------------------------------------


async def test_update_replaces_the_ingredients(connection: aiosqlite.Connection) -> None:
    service, shelter, ids = await _seed(connection)
    created = await service.create(
        ids["настойка"], "cooking", [("Мякоть", Fraction(2))], {1: Fraction(1)}
    )

    await service.update(
        created.recipe_id, ingredients=[("Спирт", Fraction(3))], yields={1: Fraction(2)}
    )

    stored = await shelter.get_recipe(created.recipe_id)
    assert stored is not None
    assert stored.ingredients == ((ids["спирт"], Fraction(3)),)


async def test_update_keeps_what_was_not_passed(connection: aiosqlite.Connection) -> None:
    service, _shelter, ids = await _seed(connection)
    created = await service.create(
        ids["настойка"], "cooking", [("Мякоть", Fraction(2))], {1: Fraction(4)}
    )

    updated = await service.update(created.recipe_id, profession_key="medicine")

    assert updated.profession_key == "medicine"
    assert [line.name for line in updated.ingredients] == ["Мякоть"]
    assert updated.yields[1] == Fraction(4)


async def test_update_refuses_an_unknown_recipe(connection: aiosqlite.Connection) -> None:
    service, _shelter, _ids = await _seed(connection)

    with pytest.raises(RecipeNotFoundError):
        await service.update(999, profession_key="cooking")


async def test_update_recomputes_the_cost(connection: aiosqlite.Connection) -> None:
    service, shelter, ids = await _seed(connection)
    created = await service.create(
        ids["настойка"], "cooking", [("Мякоть", Fraction(2))], {1: Fraction(1)}
    )

    await service.update(created.recipe_id, ingredients=[("Мякоть", Fraction(5))])

    stored = await shelter.get_cost(ids["настойка"])
    assert stored is not None
    assert stored.cost_kopeks == 5000


async def test_delete_removes_the_recipe_and_recomputes(
    connection: aiosqlite.Connection,
) -> None:
    service, shelter, ids = await _seed(connection)
    created = await service.create(
        ids["настойка"], "cooking", [("Мякоть", Fraction(2))], {1: Fraction(1)}
    )

    removed = await service.delete(created.recipe_id)

    assert removed.recipe_id == created.recipe_id
    assert await shelter.get_recipe(created.recipe_id) is None
    stored = await shelter.get_cost(ids["настойка"])
    assert stored is not None
    assert stored.source == "unresolved"  # nothing left to craft it from


async def test_delete_refuses_an_unknown_recipe(connection: aiosqlite.Connection) -> None:
    service, _shelter, _ids = await _seed(connection)

    with pytest.raises(RecipeNotFoundError):
        await service.delete(999)


# -- profession level -------------------------------------------------------


async def test_setting_the_level_changes_which_yield_applies(
    connection: aiosqlite.Connection,
) -> None:
    """The level is what decides the bonus craft — and so the cost."""
    service, _shelter, ids = await _seed(connection)
    await service.create(
        ids["настойка"],
        "cooking",
        [("Мякоть", Fraction(1))],
        {1: Fraction(1), 2: Fraction(4)},
    )

    before = (await service.show(ids["настойка"]))[0]
    await service.set_profession_level("cooking", 2)
    after = (await service.show(ids["настойка"]))[0]

    assert before.unit_cost_kopeks == 1000
    assert after.unit_cost_kopeks == 250


async def test_setting_the_level_refuses_an_unknown_profession(
    connection: aiosqlite.Connection,
) -> None:
    service, _shelter, _ids = await _seed(connection)

    with pytest.raises(UnknownProfessionError):
        await service.set_profession_level("alchemy", 2)


async def test_setting_the_level_refuses_a_level_outside_one_to_five(
    connection: aiosqlite.Connection,
) -> None:
    service, _shelter, _ids = await _seed(connection)

    with pytest.raises(ValueError, match="уровень"):
        await service.set_profession_level("cooking", 9)


# -- предметы убежки --------------------------------------------------------


async def test_add_item_makes_it_usable_as_an_ingredient(
    connection: aiosqlite.Connection,
) -> None:
    """Without this, a recipe could only use what the original import happened to include."""
    service, _shelter, ids = await _seed(connection)

    await service.add_item("Хмель", "component", my_kopeks=250)
    view = await service.create(
        ids["настойка"], "cooking", [("Хмель", Fraction(3))], {1: Fraction(1)}
    )

    assert view.ingredients[0].name == "Хмель"
    assert view.batch_cost_kopeks == 750


async def test_add_item_refuses_a_duplicate_name(connection: aiosqlite.Connection) -> None:
    service, _shelter, _ids = await _seed(connection)

    with pytest.raises(DuplicateShelterItemError):
        await service.add_item("Мякоть", "component")


async def test_setting_a_price_moves_everything_that_depends_on_it(
    connection: aiosqlite.Connection,
) -> None:
    service, shelter, ids = await _seed(connection)
    await service.create(ids["настойка"], "cooking", [("Мякоть", Fraction(2))], {1: Fraction(1)})

    await service.set_item_prices(ids["мякоть"], my_kopeks=3000)

    stored = await shelter.get_cost(ids["настойка"])
    assert stored is not None
    assert stored.cost_kopeks == 6000


async def test_clearing_a_manual_price_falls_back_to_the_market_one(
    connection: aiosqlite.Connection,
) -> None:
    """A blank «своя цена» is what makes the calculator look further (§V.2)."""
    service, shelter, ids = await _seed(connection)
    await service.set_item_prices(ids["мякоть"], market_kopeks=700)

    await service.set_item_prices(ids["мякоть"], clear_my_price=True)

    item = await shelter.get_item(ids["мякоть"])
    assert item is not None and item.my_kopeks is None
    stored = await shelter.get_cost(ids["мякоть"])
    assert stored is not None
    assert stored.cost_kopeks == 700
    assert stored.source == "market"


async def test_setting_prices_refuses_an_unknown_item(
    connection: aiosqlite.Connection,
) -> None:
    service, _shelter, _ids = await _seed(connection)

    with pytest.raises(ItemNotFoundError):
        await service.set_item_prices(999, my_kopeks=1)
