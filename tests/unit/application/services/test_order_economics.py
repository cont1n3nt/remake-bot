"""Tests for `application.services.order_economics` (заявка 13.09.2026 п.7).

`BoostOrderService`/`ShelterCostService` are stubbed: what matters here is
the arithmetic and, above all, that an unbridged line is reported rather
than silently counted as free — the whole point of the feature is that the
profit number is trustworthy or visibly incomplete.
"""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from stalbot.application.dto.boost_order_line import BoostOrderLine
from stalbot.application.services.order_economics import OrderEconomicsService, OrderLineCost
from stalbot.domain.entities.catalog_item import CatalogItem
from stalbot.domain.enums import ItemCategory
from stalbot.domain.money import Rub
from stalbot.domain.shelter.cost import CostResult

_NOW = datetime(2026, 9, 13, tzinfo=UTC)

#: A real catalog name. Bound once because every letter in it is a
#: Latin look-alike, and ruff flags the literal at every use site.
_SOUP = "Уха"  # noqa: RUF001


def _item(name: str, *, item_id: int, shelter_item_id: int | None) -> CatalogItem:
    return CatalogItem(
        id=item_id,
        name=name,
        name_norm=name.lower(),
        category=ItemCategory.BOOST,
        section=None,
        price_buy=None,
        price_sell=Rub(1000),
        emoji=None,
        sort_order=0,
        shelter_item_id=shelter_item_id,
        created_at=_NOW,
        updated_at=None,
        deleted_at=None,
    )


def _line(item_id: int, quantity: int) -> BoostOrderLine:
    return BoostOrderLine(
        channel_id=111,
        item_id=item_id,
        item_name_norm=f"item{item_id}",
        category=ItemCategory.BOOST,
        quantity=quantity,
    )


def _cost(kopeks: int | None) -> CostResult:
    return CostResult(
        cost_kopeks=kopeks,
        best_recipe_id=None,
        source="crafted" if kopeks is not None else "unresolved",
        depth=0,
        note=None,
    )


def _service(
    *,
    lines: list[tuple[BoostOrderLine, CatalogItem | None]],
    costs: dict[int, CostResult],
) -> OrderEconomicsService:
    boost_orders = MagicMock()
    boost_orders.list_lines_with_items = AsyncMock(return_value=lines)
    shelter_cost = MagicMock()
    shelter_cost.current_costs = AsyncMock(return_value=costs)
    return OrderEconomicsService(boost_orders, shelter_cost)


async def test_cost_is_unit_cost_times_quantity_in_rubles() -> None:
    service = _service(
        lines=[(_line(1, 3), _item(_SOUP, item_id=1, shelter_item_id=7))],
        costs={7: _cost(25_000)},  # 250,00 ₽ за штуку
    )

    economics = await service.for_order(111, Decimal(1000))

    assert economics.cost == Decimal(750)
    assert economics.profit == Decimal(250)
    assert economics.is_complete


async def test_margin_is_profit_as_a_share_of_revenue() -> None:
    service = _service(
        lines=[(_line(1, 1), _item(_SOUP, item_id=1, shelter_item_id=7))],
        costs={7: _cost(60_000)},  # 600 ₽
    )

    economics = await service.for_order(111, Decimal(1000))

    assert economics.margin_percent == Decimal(40)


async def test_margin_is_none_on_a_free_order() -> None:
    service = _service(lines=[(_line(1, 1), _item(_SOUP, item_id=1, shelter_item_id=7))], costs={})

    economics = await service.for_order(111, Decimal(0))

    assert economics.margin_percent is None


async def test_an_unbridged_line_is_reported_not_counted_as_free() -> None:
    """The line's cost is unknown, so it must not quietly inflate the profit."""
    service = _service(
        lines=[
            (_line(1, 1), _item(_SOUP, item_id=1, shelter_item_id=7)),
            (_line(2, 5), _item("Морфин", item_id=2, shelter_item_id=None)),
        ],
        costs={7: _cost(10_000)},
    )

    economics = await service.for_order(111, Decimal(1000))

    assert economics.cost == Decimal(100)
    assert not economics.is_complete
    assert [line.name for line in economics.unpriced] == ["Морфин"]


async def test_a_bridged_item_with_unresolved_cost_counts_as_unpriced() -> None:
    """A bridge that leads nowhere is no better than no bridge — say so either way."""
    service = _service(
        lines=[(_line(1, 2), _item(_SOUP, item_id=1, shelter_item_id=7))],
        costs={7: _cost(None)},
    )

    economics = await service.for_order(111, Decimal(500))

    assert economics.cost == Decimal(0)
    assert not economics.is_complete
    assert economics.unpriced[0].name == _SOUP


async def test_a_line_whose_item_vanished_from_the_catalog_is_skipped() -> None:
    service = _service(lines=[(_line(1, 1), None)], costs={})

    economics = await service.for_order(111, Decimal(500))

    assert economics.priced == ()
    assert economics.unpriced == ()


async def test_unpriced_line_contributes_nothing_to_its_own_total() -> None:
    assert OrderLineCost(name="X", quantity=9, unit_cost_kopeks=None).total_cost == Decimal(0)
