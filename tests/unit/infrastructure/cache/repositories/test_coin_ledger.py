"""Tests for `CoinLedgerRepository` against a real (temp-file) SQLite connection."""

from datetime import UTC, datetime

import aiosqlite
import pytest
import pytest_asyncio

from stalbot.domain.entities.coin_ledger import CoinLedgerEntry
from stalbot.domain.nick import NormalizedNick
from stalbot.infrastructure.cache.repositories.coin_ledger import CoinLedgerRepository
from stalbot.infrastructure.cache.repositories.players import PlayersRepository

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def player_id(connection: aiosqlite.Connection) -> int:
    players = PlayersRepository(connection)
    player = await players.get_or_create(NormalizedNick("scaryyyyy"), "Scaryyyyy", now=NOW)
    assert player.id is not None
    return player.id


async def test_add_assigns_an_id_and_round_trips(
    connection: aiosqlite.Connection, player_id: int
) -> None:
    repo = CoinLedgerRepository(connection)
    entry = CoinLedgerEntry(
        id=None,
        player_id=player_id,
        delta=-15,
        reason="shop:sale_coupon",
        created_by=999,
        created_at=NOW,
    )

    new_id = await repo.add(entry)
    entries = await repo.for_player(player_id)

    assert len(entries) == 1
    assert entries[0].id == new_id
    assert entries[0].delta == -15
    assert entries[0].reason == "shop:sale_coupon"


async def test_sum_for_player_with_no_entries_is_zero(
    connection: aiosqlite.Connection, player_id: int
) -> None:
    repo = CoinLedgerRepository(connection)
    assert await repo.sum_for_player(player_id) == 0


async def test_sum_for_player_sums_signed_deltas(
    connection: aiosqlite.Connection, player_id: int
) -> None:
    repo = CoinLedgerRepository(connection)
    await repo.add(
        CoinLedgerEntry(
            id=None, player_id=player_id, delta=10, reason="grant", created_by=None, created_at=NOW
        )
    )
    await repo.add(
        CoinLedgerEntry(
            id=None, player_id=player_id, delta=-3, reason="spend", created_by=None, created_at=NOW
        )
    )

    assert await repo.sum_for_player(player_id) == 7


async def test_for_player_orders_oldest_first(
    connection: aiosqlite.Connection, player_id: int
) -> None:
    repo = CoinLedgerRepository(connection)
    later = datetime(2026, 8, 11, tzinfo=UTC)
    await repo.add(
        CoinLedgerEntry(
            id=None, player_id=player_id, delta=1, reason="b", created_by=None, created_at=later
        )
    )
    await repo.add(
        CoinLedgerEntry(
            id=None, player_id=player_id, delta=2, reason="a", created_by=None, created_at=NOW
        )
    )

    entries = await repo.for_player(player_id)

    assert [e.reason for e in entries] == ["a", "b"]


async def test_zero_delta_is_rejected(connection: aiosqlite.Connection, player_id: int) -> None:
    repo = CoinLedgerRepository(connection)
    with pytest.raises(aiosqlite.IntegrityError):
        await repo.add(
            CoinLedgerEntry(
                id=None,
                player_id=player_id,
                delta=0,
                reason="oops",
                created_by=None,
                created_at=NOW,
            )
        )
