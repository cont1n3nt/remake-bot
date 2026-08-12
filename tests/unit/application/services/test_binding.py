"""Tests for `stalbot.application.services.binding.bind_discord`.

PLAN.md §6.1; sqlite_migration.md Э7. Shared by `TransactionService` and
`ManualGrantService` — covered once here rather than duplicated in both
services' test files.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest_asyncio

from stalbot.application.services.binding import bind_discord
from stalbot.domain.nick import NormalizedNick
from stalbot.infrastructure.cache.db import CacheDb
from stalbot.infrastructure.cache.repositories.players import PlayersRepository


@pytest_asyncio.fixture
async def connection(tmp_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    db = CacheDb(tmp_path / "cache.sqlite3")
    conn = await db.connect()
    yield conn
    await db.close()


class _FixedClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


_NOW = datetime(2026, 8, 2, 11, 0, tzinfo=UTC)
_CLOCK = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))


async def test_binds_an_unbound_nick(connection: aiosqlite.Connection) -> None:
    players = PlayersRepository(connection)
    await players.get_or_create(NormalizedNick("scaryyyyy"), "Scaryyyyy", now=_NOW)

    bound = await bind_discord(players, _CLOCK, NormalizedNick("scaryyyyy"), 999, force=False)

    assert bound is True
    player = await players.get_by_nick(NormalizedNick("scaryyyyy"))
    assert player is not None
    assert player.discord_id == 999


async def test_no_op_for_unknown_nick(connection: aiosqlite.Connection) -> None:
    players = PlayersRepository(connection)

    bound = await bind_discord(players, _CLOCK, NormalizedNick("nobody"), 999, force=False)

    assert bound is False


async def test_no_op_when_already_bound_to_same_id(connection: aiosqlite.Connection) -> None:
    players = PlayersRepository(connection)
    player = await players.get_or_create(NormalizedNick("scaryyyyy"), "Scaryyyyy", now=_NOW)
    assert player.id is not None
    await players.set_discord_id(player.id, 999, now=_NOW)

    bound = await bind_discord(players, _CLOCK, NormalizedNick("scaryyyyy"), 999, force=False)

    assert bound is False


async def test_does_not_rebind_without_force(connection: aiosqlite.Connection) -> None:
    players = PlayersRepository(connection)
    player = await players.get_or_create(NormalizedNick("scaryyyyy"), "Scaryyyyy", now=_NOW)
    assert player.id is not None
    await players.set_discord_id(player.id, 111, now=_NOW)

    bound = await bind_discord(players, _CLOCK, NormalizedNick("scaryyyyy"), 999, force=False)

    assert bound is False
    unchanged = await players.get_by_nick(NormalizedNick("scaryyyyy"))
    assert unchanged is not None
    assert unchanged.discord_id == 111


async def test_rebinds_when_forced(connection: aiosqlite.Connection) -> None:
    players = PlayersRepository(connection)
    player = await players.get_or_create(NormalizedNick("scaryyyyy"), "Scaryyyyy", now=_NOW)
    assert player.id is not None
    await players.set_discord_id(player.id, 111, now=_NOW)

    bound = await bind_discord(players, _CLOCK, NormalizedNick("scaryyyyy"), 999, force=True)

    assert bound is True
    updated = await players.get_by_nick(NormalizedNick("scaryyyyy"))
    assert updated is not None
    assert updated.discord_id == 999
