"""Tests for the recipe card (заявка 13.09.2026 п.3)."""

from fractions import Fraction

from stalbot.application.services.recipes import IngredientView, RecipeView
from stalbot.domain.money import format_kopeks
from stalbot.presentation.cogs.recipes.card import render_recipe, render_recipe_list
from stalbot.presentation.embeds.factory import EmbedFactory


def _line(
    name: str,
    quantity: Fraction,
    *,
    unit_cost_kopeks: int | None = 1000,
    is_energy: bool = False,
) -> IngredientView:
    return IngredientView(
        item_id=1,
        name=name,
        quantity=quantity,
        unit_cost_kopeks=unit_cost_kopeks,
        is_energy=is_energy,
    )


def _view(**overrides: object) -> RecipeView:
    defaults: dict[str, object] = {
        "recipe_id": 7,
        "output_item_id": 1,
        "output_name": "Настойка",
        "profession_key": "cooking",
        "profession_name": "Кулинария",
        "profession_level": 1,
        "ingredients": (
            _line("Мякоть", Fraction(2)),
            _line("Энергия", Fraction(900), unit_cost_kopeks=1, is_energy=True),
        ),
        "yields": {
            1: Fraction(4),
            2: Fraction(9, 2),
            3: Fraction(0),
            4: Fraction(0),
            5: Fraction(0),
        },
        "source_sheet": None,
        "source_cell": None,
    }
    defaults.update(overrides)
    return RecipeView(**defaults)  # type: ignore[arg-type]


def _render(view: RecipeView) -> str:
    return render_recipe(view, EmbedFactory()).description or ""


def test_card_names_the_profession_and_its_level() -> None:
    text = _render(_view())

    assert "Кулинария" in text
    assert "уровень 1" in text


def test_card_gives_energy_its_own_line() -> None:
    """«Сколько энергии тратится» is the owner's own question — it is answered directly."""
    text = _render(_view())

    assert "⚡ Энергия: 900" in text
    # ...and it is not repeated among the materials.
    assert text.count("Энергия") == 1


def test_card_lists_each_ingredient_with_its_share_of_the_craft() -> None:
    text = _render(_view())

    assert f"Мякоть × 2 = {format_kopeks(2000)}" in text


def test_card_shows_every_level_and_marks_the_current_one() -> None:
    """This is where bonus crafts live — the owner plans upgrades off this line."""
    text = _render(_view())

    assert "**1:4**" in text  # current level, emphasised
    assert "2:4.5" in text
    assert "3:—" in text  # locked at that level


def test_card_shows_the_unit_and_batch_cost() -> None:
    text = _render(_view())

    # (2 × 1000 + 900 × 1) / 4 = 725
    assert format_kopeks(725) in text
    assert format_kopeks(2900) in text


def test_card_says_so_when_the_recipe_is_locked_at_the_current_level() -> None:
    text = _render(_view(profession_level=3))

    assert "недоступен" in text
    assert "Себестоимость:" not in text


def test_card_marks_an_ingredient_with_no_price() -> None:
    text = _render(_view(ingredients=(_line("Мякоть", Fraction(2), unit_cost_kopeks=None),)))

    assert "цена неизвестна" in text


def test_card_credits_the_import_source_when_there_is_one() -> None:
    text = _render(_view(source_sheet="Тех. лист", source_cell="L12"))

    assert "Тех. лист" in text
    assert "L12" in text


def test_list_shows_id_item_profession_and_cost() -> None:
    embed = render_recipe_list([_view()], EmbedFactory(), title="📜 Рецепты")
    text = embed.description or ""

    assert "#7" in text
    assert "Настойка" in text
    assert "Кулинария" in text


def test_list_marks_a_recipe_locked_at_the_current_level() -> None:
    embed = render_recipe_list([_view(profession_level=3)], EmbedFactory(), title="📜 Рецепты")

    assert "🔒" in (embed.description or "")


def test_list_says_so_when_there_is_nothing() -> None:
    embed = render_recipe_list([], EmbedFactory(), title="📜 Рецепты")

    assert "Рецептов нет" in (embed.description or "")


def test_list_titles_carry_the_page_number() -> None:
    embed = render_recipe_list([_view()], EmbedFactory(), title="📜 Рецепты", page=2, pages=3)

    assert "стр. 2/3" in (embed.title or "")
