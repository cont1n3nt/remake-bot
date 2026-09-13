"""Parsing hand-typed recipe text (заявка 13.09.2026 п.3).

The owner types a recipe into a Discord modal, so both halves of it —
ingredients and per-level yields — arrive as free text and have to survive
being typed by a human at speed.

Quantities are `Fraction`, never `float`. The sheet's own yields are 4.5,
6.25 and 14.5, and `domain.shelter.cost` divides by them on every recursion
step; binary floats would drift and start disagreeing with the sheet on
deep items (that is why `RecipeSpec.units_per_craft` is a `Fraction` in the
first place).

Pure — no I/O, no database, no Discord. Every format edge case belongs in
this module's unit tests rather than being exercised through a modal.
"""

import re
from dataclasses import dataclass
from fractions import Fraction
from typing import Final

#: "Название x2" / "Название х4,5" / "Название × 6.25" — Latin x, Cyrillic
#: х and × all accepted, same as the скупка calculator's bulk paste, since
#: the owner types this by hand and the two look identical on a keyboard.
_INGREDIENT_RE: Final = re.compile(r"^(?P<name>.+?)\s*[xхX×]\s*(?P<quantity>[\d.,]+)\s*$")

#: "3:6,25" / "3 = 6.25" / "3 - 6" — one level and its yield, found anywhere
#: in the text. The lookbehind keeps "12:4" from reading as level 2, and the
#: lookahead keeps "3:6.25" from stopping after the "6".
_YIELD_RE: Final = re.compile(
    r"(?<![\d.,])(?P<level>[1-5])\s*[:=-]\s*(?P<units>\d+(?:[.,]\d+)?)(?![\d.,])"
)

MIN_LEVEL: Final = 1
MAX_LEVEL: Final = 5

#: A yield above this is a typo, not a recipe — the sheet's largest is 14.5.
_MAX_UNITS_PER_CRAFT: Final = Fraction(1000)

#: Likewise for an ingredient count.
_MAX_QUANTITY: Final = Fraction(100_000)


@dataclass(frozen=True, slots=True)
class ParsedIngredients:
    """Ingredient lines that parsed, and the raw lines that did not."""

    ingredients: tuple[tuple[str, Fraction], ...]
    """`(name as typed, quantity)` pairs, in the order they were written."""
    rejected: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ParsedYields:
    """Per-level yields that parsed, and the raw fragments that did not."""

    yields: dict[int, Fraction]
    """`{level: units per craft}`. A level the owner did not mention is
    absent, which `fill_missing_levels` turns into an explicit zero."""
    rejected: tuple[str, ...]


def _to_fraction(raw: str) -> Fraction | None:
    """Parse `"4,5"` or `"4.5"` into a `Fraction`, or `None` if it is not a number."""
    text = raw.replace(",", ".").strip()
    if not text or text.count(".") > 1:
        return None
    try:
        return Fraction(text)
    except (ValueError, ZeroDivisionError):
        return None


def parse_ingredients(text: str) -> ParsedIngredients:
    """Parse a "Название xКоличество"-per-line ingredient list.

    Энергия is an ingredient like any other here — it is a real
    `shelter_items` row (`kind='virtual'`), priced per unit, and the cost
    calculator already treats it that way. Nothing in this parser knows it
    is special.

    Args:
        text: Raw modal text, one ingredient per line. Blank lines ignored.

    Returns:
        The parsed pairs plus every line that could not be read, so the
        caller can show them back instead of silently dropping part of a
        recipe — a recipe missing one ingredient still computes a cost, it
        is just quietly wrong.
    """
    parsed: list[tuple[str, Fraction]] = []
    rejected: list[str] = []
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        match = _INGREDIENT_RE.match(stripped)
        if match is None:
            rejected.append(raw_line)
            continue
        name = match.group("name").strip()
        quantity = _to_fraction(match.group("quantity"))
        if not name or quantity is None or not (0 < quantity <= _MAX_QUANTITY):
            rejected.append(raw_line)
            continue
        parsed.append((name, quantity))
    return ParsedIngredients(ingredients=tuple(parsed), rejected=tuple(rejected))


def parse_yields(text: str) -> ParsedYields:
    """Parse per-level yields, written as `"1:4 2:4,5 3:5"` or one per line.

    Zero is meaningful and accepted: it is the sheet's «Низкий уровень» —
    the recipe exists but is not available at that profession level
    (§II.2), which the cost calculator reads as "skip this recipe".

    Args:
        text: Raw modal text. Levels may be separated by spaces, commas,
            semicolons or newlines.

    Returns:
        The levels that parsed plus every fragment that did not. A repeated
        level keeps the last value written.
    """
    parsed: dict[int, Fraction] = {}
    rejected: list[str] = []
    # Scanned rather than split: a decimal comma ("2:4,5") is
    # indistinguishable from a separator until the whole pair is matched,
    # so splitting first cuts valid values in half. Whatever the scan does
    # not consume is what the owner mistyped.
    leftover = []
    last_end = 0
    for match in _YIELD_RE.finditer(text):
        leftover.append(text[last_end : match.start()])
        last_end = match.end()
        units = _to_fraction(match.group("units"))
        if units is None or not (0 <= units <= _MAX_UNITS_PER_CRAFT):
            rejected.append(match.group(0))
            continue
        parsed[int(match.group("level"))] = units
    leftover.append(text[last_end:])

    for chunk in leftover:
        for token in chunk.split():
            stripped = token.strip(",;")
            if stripped:
                rejected.append(stripped)
    return ParsedYields(yields=parsed, rejected=tuple(rejected))


def fill_missing_levels(yields: dict[int, Fraction]) -> dict[int, Fraction]:
    """Return yields for all five levels, with anything unmentioned set to zero.

    `recipe_yields` is keyed by `(recipe_id, level)` and a missing row is
    already read as zero by `load_recipe_specs_for_current_levels`. Writing
    the zero explicitly makes the stored recipe say what it means —
    "unavailable at this level" — instead of leaving the reader to infer it
    from an absent row.

    Args:
        yields: Whatever `parse_yields` recovered.
    """
    return {level: yields.get(level, Fraction(0)) for level in range(MIN_LEVEL, MAX_LEVEL + 1)}


def format_quantity(value: Fraction) -> str:
    """Render a quantity the way it would be typed back in: `4.5`, not `9/2`."""
    if value.denominator == 1:
        return str(value.numerator)
    text = f"{float(value):.4f}".rstrip("0").rstrip(".")
    return text


def format_ingredients(ingredients: tuple[tuple[str, Fraction], ...]) -> str:
    """Render ingredients back into the text format the modal accepts.

    Args:
        ingredients: `(name, quantity)` pairs.
    """
    return "\n".join(f"{name} x{format_quantity(quantity)}" for name, quantity in ingredients)


def format_yields(yields: dict[int, Fraction]) -> str:
    """Render yields back into the text format the modal accepts.

    Args:
        yields: `{level: units per craft}`.
    """
    return " ".join(
        f"{level}:{format_quantity(yields[level])}" for level in sorted(yields) if level in yields
    )
