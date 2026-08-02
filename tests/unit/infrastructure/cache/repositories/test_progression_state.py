"""Tests for `ProgressionStateRepository` against a real (temp-file) SQLite connection."""

from datetime import UTC, datetime

import aiosqlite

from stalbot.application.dto.progression_state import ProgressionState
from stalbot.domain.nick import NormalizedNick
from stalbot.infrastructure.cache.repositories.progression_state import ProgressionStateRepository


async def test_get_returns_none_when_untracked(connection: aiosqlite.Connection) -> None:
    repo = ProgressionStateRepository(connection)
    assert await repo.get(NormalizedNick("scaryyyyy")) is None


async def test_upsert_then_get_round_trips(connection: aiosqlite.Connection) -> None:
    repo = ProgressionStateRepository(connection)
    state = ProgressionState(
        nick=NormalizedNick("scaryyyyy"),
        last_rank="💎 Elite",
        last_referral_role="🧲 Вербовщик",
        announced_at=datetime(2026, 7, 31, 21, 45, tzinfo=UTC),
    )

    await repo.upsert(state)
    result = await repo.get(NormalizedNick("scaryyyyy"))

    assert result == state


async def test_upsert_overwrites_previous_state(connection: aiosqlite.Connection) -> None:
    repo = ProgressionStateRepository(connection)
    nick = NormalizedNick("scaryyyyy")
    await repo.upsert(
        ProgressionState(
            nick=nick, last_rank="🔹 Standard", last_referral_role=None, announced_at=None
        )
    )
    await repo.upsert(
        ProgressionState(
            nick=nick, last_rank="🔷 Premium", last_referral_role=None, announced_at=None
        )
    )

    result = await repo.get(nick)

    assert result is not None
    assert result.last_rank == "🔷 Premium"


async def test_announced_at_none_round_trips(connection: aiosqlite.Connection) -> None:
    repo = ProgressionStateRepository(connection)
    nick = NormalizedNick("scaryyyyy")
    await repo.upsert(
        ProgressionState(nick=nick, last_rank=None, last_referral_role=None, announced_at=None)
    )

    result = await repo.get(nick)

    assert result is not None
    assert result.announced_at is None


async def test_manual_rank_role_defaults_to_false(connection: aiosqlite.Connection) -> None:
    repo = ProgressionStateRepository(connection)
    nick = NormalizedNick("scaryyyyy")
    await repo.upsert(
        ProgressionState(nick=nick, last_rank=None, last_referral_role=None, announced_at=None)
    )

    result = await repo.get(nick)

    assert result is not None
    assert result.manual_rank_role is False


async def test_manual_rank_role_true_round_trips(connection: aiosqlite.Connection) -> None:
    repo = ProgressionStateRepository(connection)
    nick = NormalizedNick("scaryyyyy")
    await repo.upsert(
        ProgressionState(
            nick=nick,
            last_rank="💎 Elite",
            last_referral_role=None,
            announced_at=None,
            manual_rank_role=True,
        )
    )

    result = await repo.get(nick)

    assert result is not None
    assert result.manual_rank_role is True
