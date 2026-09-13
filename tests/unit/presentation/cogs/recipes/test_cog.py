"""Tests for `presentation.cogs.recipes.cog.RecipesCog` (заявка 13.09.2026 п.3).

`RecipeService` is mocked — its own behaviour is covered in
`tests/unit/application/services/test_recipes.py`. What matters here is the
modal round trip: Discord hands a modal submission no memory of the command
that opened it, so the cog has to carry the target across, and it has to
refuse a recipe it could not fully read rather than write half of it.
"""

from fractions import Fraction
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from stalbot.application.services.recipes import IngredientView, RecipeView
from stalbot.presentation.cogs.recipes.cog import RecipesCog
from stalbot.presentation.cogs.recipes.modals import RecipeFormModal
from stalbot.presentation.embeds.factory import EmbedFactory


def _view(**overrides: object) -> RecipeView:
    defaults: dict[str, object] = {
        "recipe_id": 7,
        "output_item_id": 1,
        "output_name": "Настойка",
        "profession_key": "cooking",
        "profession_name": "Кулинария",
        "profession_level": 1,
        "ingredients": (
            IngredientView(
                item_id=1,
                name="Мякоть",
                quantity=Fraction(2),
                unit_cost_kopeks=1000,
                is_energy=False,
            ),
        ),
        "yields": {1: Fraction(4)},
        "source_sheet": None,
        "source_cell": None,
    }
    defaults.update(overrides)
    return RecipeView(**defaults)  # type: ignore[arg-type]


def _cog(*, recipes: MagicMock | None = None) -> tuple[RecipesCog, MagicMock]:
    recipes = recipes or MagicMock()
    recipes.get = AsyncMock(return_value=_view())
    recipes.create = AsyncMock(return_value=_view())
    recipes.update = AsyncMock(return_value=_view())
    recipes.delete = AsyncMock(return_value=_view())
    recipes.show = AsyncMock(return_value=[_view()])
    recipes.list_recipes = AsyncMock(return_value=[_view()])
    recipes.professions = AsyncMock(return_value={"cooking": ("Кулинария", 1)})
    recipes.set_profession_level = AsyncMock(return_value=("Кулинария", 2))
    shelter = MagicMock()
    shelter.get_item = AsyncMock(return_value=None)
    shelter.all_items = AsyncMock(return_value=[])
    return RecipesCog(recipes, shelter, EmbedFactory()), recipes


def _interaction(user_id: int = 1) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock(spec=discord.Member, id=user_id)
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.response.send_modal = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock(return_value=MagicMock(spec=discord.Message))
    return interaction


async def test_add_opens_an_empty_form() -> None:
    cog, _recipes = _cog()
    interaction = _interaction()

    callback: Any = RecipesCog.recipe_add.callback
    await callback(cog, interaction, 1, "cooking")

    modal = interaction.response.send_modal.call_args.args[0]
    assert isinstance(modal, RecipeFormModal)
    assert modal.ingredients.default is None


async def test_edit_opens_a_form_prefilled_with_the_current_recipe() -> None:
    cog, _recipes = _cog()
    interaction = _interaction()

    callback: Any = RecipesCog.recipe_edit.callback
    await callback(cog, interaction, 7, None)

    modal = interaction.response.send_modal.call_args.args[0]
    assert modal.ingredients.default == "Мякоть x2"
    assert modal.yields.default == "1:4"


async def test_submitting_after_add_creates_the_recipe() -> None:
    cog, recipes = _cog()
    open_interaction = _interaction()
    add: Any = RecipesCog.recipe_add.callback
    await add(cog, open_interaction, 1, "cooking")

    submit = _interaction()
    await cog._on_recipe_submitted(submit, "Мякоть x2", "1:4")

    recipes.create.assert_awaited_once()
    args, _kwargs = recipes.create.call_args
    assert args[0] == 1
    assert args[1] == "cooking"
    assert args[2] == (("Мякоть", Fraction(2)),)
    assert args[3] == {1: Fraction(4)}


async def test_submitting_after_edit_updates_the_recipe() -> None:
    cog, recipes = _cog()
    open_interaction = _interaction()
    edit: Any = RecipesCog.recipe_edit.callback
    await edit(cog, open_interaction, 7, "medicine")

    submit = _interaction()
    await cog._on_recipe_submitted(submit, "Мякоть x2", "1:4")

    recipes.update.assert_awaited_once()
    args, kwargs = recipes.update.call_args
    assert args[0] == 7
    assert kwargs["profession_key"] == "medicine"


async def test_an_unreadable_line_refuses_the_whole_recipe() -> None:
    """Writing the readable half would produce a cost that is quietly wrong."""
    cog, recipes = _cog()
    open_interaction = _interaction()
    add: Any = RecipesCog.recipe_add.callback
    await add(cog, open_interaction, 1, "cooking")

    submit = _interaction()
    await cog._on_recipe_submitted(submit, "Мякоть x2\nчто-то не то", "1:4")

    recipes.create.assert_not_called()
    embed = submit.followup.send.call_args.kwargs["embed"]
    description = embed.description or ""
    assert "не сохранён" in description
    assert "что-то не то" in description


async def test_an_unreadable_yield_also_refuses_the_recipe() -> None:
    cog, recipes = _cog()
    open_interaction = _interaction()
    add: Any = RecipesCog.recipe_add.callback
    await add(cog, open_interaction, 1, "cooking")

    submit = _interaction()
    await cog._on_recipe_submitted(submit, "Мякоть x2", "1:4 ерунда")

    recipes.create.assert_not_called()


async def test_a_submission_with_no_pending_command_is_refused() -> None:
    """A modal that outlived its command — e.g. after a restart — must not guess."""
    cog, recipes = _cog()
    submit = _interaction()

    await cog._on_recipe_submitted(submit, "Мякоть x2", "1:4")

    recipes.create.assert_not_called()
    recipes.update.assert_not_called()
    embed = submit.followup.send.call_args.kwargs["embed"]
    assert "устарела" in (embed.description or "")


async def test_two_admins_editing_at_once_do_not_collide() -> None:
    """The pending target is per user, not per cog."""
    cog, recipes = _cog()
    add: Any = RecipesCog.recipe_add.callback
    edit: Any = RecipesCog.recipe_edit.callback
    await add(cog, _interaction(user_id=1), 1, "cooking")
    await edit(cog, _interaction(user_id=2), 7, None)

    await cog._on_recipe_submitted(_interaction(user_id=2), "Мякоть x2", "1:4")
    await cog._on_recipe_submitted(_interaction(user_id=1), "Мякоть x2", "1:4")

    recipes.update.assert_awaited_once()
    recipes.create.assert_awaited_once()


async def test_show_says_so_when_an_item_has_no_recipe() -> None:
    cog, recipes = _cog()
    recipes.show = AsyncMock(return_value=[])
    interaction = _interaction()

    callback: Any = RecipesCog.recipe_show.callback
    await callback(cog, interaction, 1)

    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "Рецептов нет" in (embed.title or "")


async def test_level_reports_the_recompute() -> None:
    cog, recipes = _cog()
    interaction = _interaction()

    callback: Any = RecipesCog.recipe_level.callback
    await callback(cog, interaction, "cooking", 2)

    recipes.set_profession_level.assert_awaited_once_with("cooking", 2)
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "пересчитана" in (embed.description or "")


async def test_delete_is_abandoned_when_not_confirmed() -> None:
    cog, recipes = _cog()
    cog._confirm_delete = AsyncMock(return_value=False)  # type: ignore[method-assign]
    interaction = _interaction()

    callback: Any = RecipesCog.recipe_delete.callback
    await callback(cog, interaction, 7)

    recipes.delete.assert_not_called()
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "Отменено" in (embed.title or "")


async def test_delete_removes_the_recipe_once_confirmed() -> None:
    cog, recipes = _cog()
    cog._confirm_delete = AsyncMock(return_value=True)  # type: ignore[method-assign]
    interaction = _interaction()

    callback: Any = RecipesCog.recipe_delete.callback
    await callback(cog, interaction, 7)

    recipes.delete.assert_awaited_once_with(7)
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "удалён" in (embed.title or "")
    assert "пересчитана" in (embed.description or "")
