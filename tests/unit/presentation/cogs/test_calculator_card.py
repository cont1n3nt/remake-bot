"""Tests for `stalbot.presentation.cogs.calculator_card.render_calculator_body` (Э8)."""

from datetime import UTC, datetime
from decimal import Decimal

from stalbot.application.dto.boost_order_line import BoostOrderLine
from stalbot.domain.entities.catalog_item import CatalogItem
from stalbot.domain.enums import ItemCategory
from stalbot.domain.money import Rub
from stalbot.presentation.cogs.calculator_card import render_calculator_body

_NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)


def _item(
    name: str,
    *,
    category: ItemCategory,
    price_buy: int | None = None,
    price_sell: int | None = None,
) -> CatalogItem:
    return CatalogItem(
        id=1,
        name=name,
        name_norm=name.lower(),
        category=category,
        section=None,
        price_buy=Rub(price_buy) if price_buy is not None else None,
        price_sell=Rub(price_sell) if price_sell is not None else None,
        emoji=None,
        sort_order=0,
        shelter_item_id=None,
        created_at=_NOW,
        updated_at=None,
        deleted_at=None,
    )


def _line(item_id: int, quantity: int) -> BoostOrderLine:
    return BoostOrderLine(
        channel_id=111,
        item_id=item_id,
        item_name_norm="x",
        category=ItemCategory.RESOURCE,
        quantity=quantity,
    )


def test_render_calculator_body_shows_empty_state_placeholder() -> None:
    body = render_calculator_body([], Decimal(0))

    assert "Список пока пуст" in body


def test_render_calculator_body_lists_resources_at_price_buy() -> None:
    item = _item("Кристалл", category=ItemCategory.RESOURCE, price_buy=100)
    body = render_calculator_body([(_line(1, 3), item)], Decimal(300))

    assert "Кристалл × 3" in body
    assert "Позиций: 1" in body


def test_render_calculator_body_lists_boosts_at_price_sell() -> None:
    item = _item("Топот", category=ItemCategory.BOOST, price_sell=300000)
    body = render_calculator_body([(_line(1, 1), item)], Decimal(300000))

    assert "Топот × 1" in body


def test_render_calculator_body_omits_lines_with_a_deleted_item() -> None:
    body = render_calculator_body([(_line(1, 3), None)], Decimal(0))

    assert "Список пока пуст" in body
    assert "Позиций: 0" in body


def test_render_calculator_body_shows_the_total() -> None:
    body = render_calculator_body([], Decimal(12345))

    assert "Итого" in body
