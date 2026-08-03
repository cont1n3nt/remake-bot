"""Tests for `stalbot.presentation.autocomplete.item_choices` (PLAN.md §10.7, §10.9)."""

from decimal import Decimal

from stalbot.domain.entities.item import Item
from stalbot.domain.enums import ItemCategory
from stalbot.presentation.autocomplete import item_choices


def _item(**overrides: object) -> Item:
    defaults: dict[str, object] = {
        "id": 1,
        "name": "Хвост тушкана",
        "category": ItemCategory.RESOURCE,
        "price_buy": Decimal(18000),
        "price_sell": None,
        "emoji": None,
        "updated_at": None,
        "row": 3,
    }
    defaults.update(overrides)
    return Item(**defaults)  # type: ignore[arg-type]


def test_empty_query_returns_every_item_up_to_the_limit() -> None:
    items = [_item(id=1, name="Топот"), _item(id=2, name="Кристалл")]
    choices = item_choices(items, "")
    assert {c.value for c in choices} == {1, 2}


def test_substring_match_ranks_above_fuzzy_match() -> None:
    items = [_item(id=1, name="Кристалл"), _item(id=2, name="Топот")]
    choices = item_choices(items, "топ")
    assert choices[0].value == 2


def test_category_filter_excludes_the_other_category() -> None:
    items = [
        _item(id=1, name="Топот", category=ItemCategory.BOOST),
        _item(id=2, name="Топот", category=ItemCategory.RESOURCE),
    ]
    choices = item_choices(items, "топот", category=ItemCategory.BOOST)
    assert [c.value for c in choices] == [1]


def test_choice_value_is_the_item_id_not_the_name() -> None:
    items = [_item(id=42, name="Кристалл")]
    (choice,) = item_choices(items, "крист")
    assert choice.value == 42
    assert "Кристалл" in choice.name


def test_choice_name_includes_current_price() -> None:
    items = [_item(id=1, name="Кристалл", price_buy=Decimal(120000))]
    (choice,) = item_choices(items, "крист")
    assert "120" in choice.name


def test_choice_name_reports_when_there_is_no_price() -> None:
    items = [_item(id=1, name="Кристалл", price_buy=None)]
    (choice,) = item_choices(items, "крист")
    assert "цены нет" in choice.name


def test_query_with_no_reasonable_match_returns_nothing() -> None:
    items = [_item(id=1, name="Кристалл")]
    assert item_choices(items, "zzzzzzzzzzzz") == []


def test_results_are_capped_at_25() -> None:
    items = [_item(id=i, name=f"Предмет {i}") for i in range(40)]
    choices = item_choices(items, "")
    assert len(choices) == 25
