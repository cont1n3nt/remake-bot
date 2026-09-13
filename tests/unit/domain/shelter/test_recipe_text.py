"""Tests for `domain.shelter.recipe_text` (заявка 13.09.2026 п.3).

Every format edge case lives here rather than being exercised through a
Discord modal — the owner types these by hand, so what the parser tolerates
and what it refuses is the whole design.
"""

from fractions import Fraction

from stalbot.domain.shelter.recipe_text import (
    fill_missing_levels,
    format_ingredients,
    format_quantity,
    format_yields,
    parse_ingredients,
    parse_yields,
)

# -- ingredients ------------------------------------------------------------


def test_parses_one_ingredient_per_line() -> None:
    result = parse_ingredients("Мякоть x2\nЭнергия x900")

    assert result.ingredients == (("Мякоть", Fraction(2)), ("Энергия", Fraction(900)))
    assert result.rejected == ()


def test_accepts_every_multiplication_sign_the_keyboard_offers() -> None:
    """Latin x, Cyrillic х and × look identical when typed at speed."""
    result = parse_ingredients("Мякоть x1\nМякоть х2\nМякоть ×3\nМякоть X4")

    assert [quantity for _name, quantity in result.ingredients] == [
        Fraction(1),
        Fraction(2),
        Fraction(3),
        Fraction(4),
    ]


def test_accepts_a_comma_as_the_decimal_separator() -> None:
    result = parse_ingredients("Мякоть x4,5")

    assert result.ingredients == (("Мякоть", Fraction(9, 2)),)


def test_quantities_are_exact_not_floating_point() -> None:
    """`domain.shelter.cost` divides by these on every recursion step."""
    result = parse_ingredients("Мякоть x6,25")

    assert result.ingredients[0][1] == Fraction(25, 4)


def test_a_name_may_contain_spaces_and_digits() -> None:
    result = parse_ingredients("Сумка 12.7 Easy Mode x2")

    assert result.ingredients == (("Сумка 12.7 Easy Mode", Fraction(2)),)


def test_blank_lines_are_ignored() -> None:
    result = parse_ingredients("Мякоть x2\n\n   \nЭнергия x900")

    assert len(result.ingredients) == 2
    assert result.rejected == ()


def test_a_line_with_no_quantity_is_reported_not_dropped() -> None:
    """A recipe missing one ingredient still computes a cost — a quietly wrong one."""
    result = parse_ingredients("Мякоть x2\nпросто текст")

    assert result.ingredients == (("Мякоть", Fraction(2)),)
    assert result.rejected == ("просто текст",)


def test_zero_and_negative_quantities_are_rejected() -> None:
    result = parse_ingredients("Мякоть x0\nЭнергия x-5")

    assert result.ingredients == ()
    assert len(result.rejected) == 2


def test_an_absurd_quantity_is_rejected() -> None:
    result = parse_ingredients("Мякоть x999999999")

    assert result.ingredients == ()
    assert result.rejected == ("Мякоть x999999999",)


def test_a_quantity_with_two_dots_is_rejected() -> None:
    result = parse_ingredients("Мякоть x4.5.6")

    assert result.ingredients == ()
    assert result.rejected == ("Мякоть x4.5.6",)


def test_a_missing_name_is_rejected() -> None:
    result = parse_ingredients("x5")

    assert result.ingredients == ()
    assert result.rejected == ("x5",)


# -- yields -----------------------------------------------------------------


def test_parses_yields_written_on_one_line() -> None:
    result = parse_yields("1:4 2:4,5 3:5 4:6 5:6,5")

    assert result.yields == {
        1: Fraction(4),
        2: Fraction(9, 2),
        3: Fraction(5),
        4: Fraction(6),
        5: Fraction(13, 2),
    }
    assert result.rejected == ()


def test_parses_yields_written_one_per_line() -> None:
    result = parse_yields("1: 4\n2: 4.5\n3: 5")

    assert result.yields == {1: Fraction(4), 2: Fraction(9, 2), 3: Fraction(5)}


def test_accepts_equals_and_dash_as_separators() -> None:
    result = parse_yields("1=4\n2-5")

    assert result.yields == {1: Fraction(4), 2: Fraction(5)}


def test_zero_is_a_real_value_meaning_unavailable_at_that_level() -> None:
    """The sheet's «Низкий уровень» (§II.2) — the recipe exists but is locked."""
    result = parse_yields("1:0 2:0 3:5")

    assert result.yields[1] == Fraction(0)
    assert result.yields[3] == Fraction(5)
    assert result.rejected == ()


def test_a_level_outside_one_to_five_is_rejected() -> None:
    result = parse_yields("0:4 6:5 3:5")

    assert result.yields == {3: Fraction(5)}
    assert len(result.rejected) == 2


def test_a_repeated_level_keeps_the_last_value() -> None:
    result = parse_yields("3:5 3:7")

    assert result.yields == {3: Fraction(7)}


def test_unreadable_yield_fragments_are_reported() -> None:
    result = parse_yields("1:4 abc 3:5")

    assert result.yields == {1: Fraction(4), 3: Fraction(5)}
    assert result.rejected == ("abc",)


def test_fill_missing_levels_writes_the_zeros_out() -> None:
    filled = fill_missing_levels({3: Fraction(5)})

    assert filled == {
        1: Fraction(0),
        2: Fraction(0),
        3: Fraction(5),
        4: Fraction(0),
        5: Fraction(0),
    }


# -- round trip -------------------------------------------------------------


def test_format_quantity_reads_back_as_it_was_typed() -> None:
    assert format_quantity(Fraction(2)) == "2"
    assert format_quantity(Fraction(9, 2)) == "4.5"
    assert format_quantity(Fraction(25, 4)) == "6.25"


def test_ingredients_survive_a_format_parse_round_trip() -> None:
    """`/recipe edit` pre-fills the modal with this — it has to parse back identically."""
    original = (("Мякоть", Fraction(9, 2)), ("Энергия", Fraction(900)))

    assert parse_ingredients(format_ingredients(original)).ingredients == original


def test_yields_survive_a_format_parse_round_trip() -> None:
    original = {1: Fraction(0), 2: Fraction(9, 2), 3: Fraction(25, 4)}

    assert parse_yields(format_yields(original)).yields == original
