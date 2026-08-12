"""Tests for `stalbot.presentation.autocomplete.item_choices` (PLAN.md §10.7, §10.9)."""

from datetime import UTC, datetime

from stalbot.domain.entities.catalog_item import CatalogItem
from stalbot.domain.enums import ItemCategory
from stalbot.domain.money import Rub
from stalbot.presentation.autocomplete import item_choices

_NOW = datetime(2026, 8, 10, tzinfo=UTC)


def _item(**overrides: object) -> CatalogItem:
    defaults: dict[str, object] = {
        "id": 1,
        "name": "Хвост тушкана",
        "name_norm": "хвост тушкана",
        "category": ItemCategory.RESOURCE,
        "section": None,
        "price_buy": Rub(18000),
        "price_sell": None,
        "emoji": None,
        "sort_order": 0,
        "shelter_item_id": None,
        "created_at": _NOW,
        "updated_at": None,
        "deleted_at": None,
    }
    defaults.update(overrides)
    return CatalogItem(**defaults)  # type: ignore[arg-type]


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
        _item(id=1, name="Топот", category=ItemCategory.BOOST, price_buy=None, price_sell=Rub(1)),
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
    items = [_item(id=1, name="Кристалл", price_buy=Rub(120000))]
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


def test_items_without_an_id_are_skipped() -> None:
    """A not-yet-persisted item (`id=None`) has nothing an autocomplete `Choice[int]` could hold."""
    items = [_item(id=None, name="Черновик")]
    assert item_choices(items, "") == []
