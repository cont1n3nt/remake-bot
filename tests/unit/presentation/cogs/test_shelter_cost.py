"""Tests for the pure formatting helpers in `presentation.cogs.shelter_cost`."""

from stalbot.application.dto.precost_diff import PrecostDiff
from stalbot.domain.shelter.cost import CostResult
from stalbot.presentation.cogs.shelter_cost import _cost_text, _format_diffs, _format_result


def test_cost_text_formats_kopeks() -> None:
    assert _cost_text(1000) == "10,00 ₽"


def test_cost_text_shows_a_dash_when_unresolved() -> None:
    assert _cost_text(None) == "—"


def test_format_result_shows_cost_and_source_label() -> None:
    result = CostResult(
        cost_kopeks=1000, best_recipe_id=None, source="my_price", depth=0, note=None
    )

    text = _format_result(result)

    assert "10,00 ₽" in text
    assert "своя цена" in text


def test_format_result_includes_the_note_when_present() -> None:
    result = CostResult(
        cost_kopeks=None, best_recipe_id=None, source="unresolved", depth=0, note="cycle detected"
    )

    text = _format_result(result)

    assert "cycle detected" in text


def test_format_diffs_shows_before_and_after_per_item() -> None:
    diffs = [
        PrecostDiff(item_id=1, item_name="Мякоть", before_kopeks=1000, after_kopeks=3000),
        PrecostDiff(item_id=2, item_name="Настойка", before_kopeks=None, after_kopeks=3000),
    ]

    text = _format_diffs(diffs)

    assert "Мякоть" in text
    assert "10,00 ₽ → 30,00 ₽" in text
    assert "Настойка" in text
    assert "— → 30,00 ₽" in text
