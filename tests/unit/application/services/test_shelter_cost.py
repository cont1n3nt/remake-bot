"""Tests for `stalbot.application.services.shelter_cost.ShelterCostService` (§V.2).

Real, SQLite-backed `ShelterRepository` (same approach as
`test_boost_orders.py`) — a small synthetic crafting chain: `Мякоть` is an
ingredient of `Настойка`, which is in turn an ingredient of `Эликсир`, so a
`/precost` on `Мякоть` must ripple through both.
"""

from datetime import UTC, datetime
from fractions import Fraction

import aiosqlite
import pytest

from stalbot.application.services.shelter_cost import ShelterCostService
from stalbot.domain.entities.shelter_item import ShelterItem
from stalbot.domain.errors import ItemNotFoundError
from stalbot.infrastructure.cache.repositories.shelter import RecipeImport, ShelterRepository

_NOW = datetime(2026, 8, 21, tzinfo=UTC)


def _item(name: str, *, kind: str = "component", my_kopeks: int | None = None) -> ShelterItem:
    return ShelterItem(
        id=None,
        name=name,
        name_norm=name.lower(),
        kind=kind,
        market_kopeks=None,
        my_kopeks=my_kopeks,
        vendor_kopeks=None,
        updated_at=_NOW,
    )


async def _build_chain(
    connection: aiosqlite.Connection,
) -> tuple[ShelterRepository, dict[str, int]]:
    """`Мякоть` (my_kopeks=1000) -> `Настойка` (1 Мякоть) -> `Эликсир` (1 Настойка)."""
    shelter = ShelterRepository(connection)
    item_ids = await shelter.insert_items(
        [
            _item("мякоть", my_kopeks=1000),
            _item("настойка", kind="craftable"),
            _item("эликсир", kind="craftable"),
        ]
    )
    await shelter.insert_recipes(
        [
            RecipeImport(
                output_name_norm="настойка",
                profession_key="cooking",
                source_sheet=None,
                source_cell=None,
                ingredients=(("мякоть", Fraction(1)),),
                yields_by_level={1: Fraction(1)},
            ),
            RecipeImport(
                output_name_norm="эликсир",
                profession_key="cooking",
                source_sheet=None,
                source_cell=None,
                ingredients=(("настойка", Fraction(1)),),
                yields_by_level={1: Fraction(1)},
            ),
        ],
        item_ids,
    )
    return shelter, item_ids


async def test_current_costs_resolves_the_whole_chain(connection: aiosqlite.Connection) -> None:
    shelter, item_ids = await _build_chain(connection)
    service = ShelterCostService(shelter)

    costs = await service.current_costs()

    assert costs[item_ids["мякоть"]].cost_kopeks == 1000
    assert costs[item_ids["настойка"]].cost_kopeks == 1000
    assert costs[item_ids["эликсир"]].cost_kopeks == 1000


async def test_precost_ripples_through_every_transitive_consumer(
    connection: aiosqlite.Connection,
) -> None:
    shelter, item_ids = await _build_chain(connection)
    service = ShelterCostService(shelter)

    diffs = await service.precost(item_ids["мякоть"], 3000)

    affected = {diff.item_id: diff for diff in diffs}
    assert affected[item_ids["мякоть"]].before_kopeks == 1000
    assert affected[item_ids["мякоть"]].after_kopeks == 3000
    assert affected[item_ids["настойка"]].before_kopeks == 1000
    assert affected[item_ids["настойка"]].after_kopeks == 3000
    assert affected[item_ids["эликсир"]].before_kopeks == 1000
    assert affected[item_ids["эликсир"]].after_kopeks == 3000


async def test_precost_omits_items_whose_cost_does_not_change(
    connection: aiosqlite.Connection,
) -> None:
    shelter = ShelterRepository(connection)
    item_ids = await shelter.insert_items(
        [_item("мякоть", my_kopeks=1000), _item("незав.", my_kopeks=500)]
    )
    service = ShelterCostService(shelter)

    diffs = await service.precost(item_ids["мякоть"], 3000)

    assert {diff.item_id for diff in diffs} == {item_ids["мякоть"]}


async def test_precost_at_the_same_price_reports_no_changes(
    connection: aiosqlite.Connection,
) -> None:
    shelter, item_ids = await _build_chain(connection)
    service = ShelterCostService(shelter)

    diffs = await service.precost(item_ids["мякоть"], 1000)

    assert diffs == []


async def test_precost_rejects_an_unknown_item(connection: aiosqlite.Connection) -> None:
    shelter = ShelterRepository(connection)
    service = ShelterCostService(shelter)

    with pytest.raises(ItemNotFoundError):
        await service.precost(999, 1000)
