"""Tests for `DealsRepository` against a real (temp-file) SQLite connection."""

from dataclasses import replace
from datetime import UTC, datetime

import aiosqlite
import pytest
import pytest_asyncio

from stalbot.domain.entities.deal import Deal
from stalbot.domain.enums import DealSource, DealType, OccurredAtKind
from stalbot.domain.money import Rub
from stalbot.domain.nick import NormalizedNick
from stalbot.infrastructure.cache.repositories.deals import DealsRepository
from stalbot.infrastructure.cache.repositories.players import PlayersRepository

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)

_BASE_DEAL = Deal(
    id=None,
    player_id=0,
    occurred_at=NOW,
    occurred_at_kind=OccurredAtKind.BOT,
    deal_type=DealType.SALE,
    amount=Rub(500_000),
    coins=0,
    xp=0,
    rank_at_deal=None,
    booster_at_deal=False,
    recorded_by=999,
    source=DealSource.ADD,
    legacy_sheet_row=None,
    created_at=NOW,
)


def _deal(player_id: int, **overrides: object) -> Deal:
    return replace(_BASE_DEAL, player_id=player_id, **overrides)  # type: ignore[arg-type]


@pytest_asyncio.fixture
async def player_id(connection: aiosqlite.Connection) -> int:
    players = PlayersRepository(connection)
    player = await players.get_or_create(NormalizedNick("scaryyyyy"), "Scaryyyyy", now=NOW)
    assert player.id is not None
    return player.id


async def test_insert_assigns_an_id_and_round_trips(
    connection: aiosqlite.Connection, player_id: int
) -> None:
    repo = DealsRepository(connection)
    deal = _deal(player_id, amount=Rub(1_500_000), coins=1, xp=10)

    new_id = await repo.insert(deal)
    stored = await repo.get_by_id(new_id)

    assert stored is not None
    assert stored.id == new_id
    assert stored.player_id == player_id
    assert stored.amount == 1_500_000
    assert stored.coins == 1
    assert stored.xp == 10
    assert stored.occurred_at == NOW
    assert stored.occurred_at_kind is OccurredAtKind.BOT
    assert stored.deal_type is DealType.SALE
    assert stored.source is DealSource.ADD


async def test_insert_many_persists_every_deal(
    connection: aiosqlite.Connection, player_id: int
) -> None:
    repo = DealsRepository(connection)
    deals = [_deal(player_id, legacy_sheet_row=n) for n in range(3, 6)]

    await repo.insert_many(deals)

    assert await repo.count() == 3
    for_player = await repo.for_player(player_id)
    assert [d.legacy_sheet_row for d in for_player] == [3, 4, 5]


async def test_insert_many_with_empty_sequence_is_a_no_op(connection: aiosqlite.Connection) -> None:
    repo = DealsRepository(connection)
    await repo.insert_many([])
    assert await repo.count() == 0


async def test_for_player_orders_by_occurred_at(
    connection: aiosqlite.Connection, player_id: int
) -> None:
    repo = DealsRepository(connection)
    later = datetime(2026, 8, 11, tzinfo=UTC)
    earlier = datetime(2026, 8, 9, tzinfo=UTC)
    await repo.insert(_deal(player_id, occurred_at=later, legacy_sheet_row=2))
    await repo.insert(_deal(player_id, occurred_at=earlier, legacy_sheet_row=1))

    ordered = await repo.for_player(player_id)

    assert [d.legacy_sheet_row for d in ordered] == [1, 2]


async def test_negative_amount_is_rejected(
    connection: aiosqlite.Connection, player_id: int
) -> None:
    repo = DealsRepository(connection)
    with pytest.raises(aiosqlite.IntegrityError):
        await repo.insert(_deal(player_id, amount=Rub(-1)))


async def test_unknown_player_id_is_rejected(connection: aiosqlite.Connection) -> None:
    repo = DealsRepository(connection)
    with pytest.raises(aiosqlite.IntegrityError):
        await repo.insert(_deal(999_999))
