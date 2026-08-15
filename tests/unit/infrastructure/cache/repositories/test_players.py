"""Tests for `PlayersRepository` against a real (temp-file) SQLite connection."""

from datetime import UTC, datetime

import aiosqlite
import pytest

from stalbot.domain.nick import NormalizedNick
from stalbot.infrastructure.cache.repositories.players import PlayersRepository

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


async def test_get_by_id_returns_none_when_missing(connection: aiosqlite.Connection) -> None:
    repo = PlayersRepository(connection)
    assert await repo.get_by_id(1) is None


async def test_get_or_create_creates_a_new_player(connection: aiosqlite.Connection) -> None:
    repo = PlayersRepository(connection)

    player = await repo.get_or_create(NormalizedNick("scaryyyyy"), "Scaryyyyy", now=NOW)

    assert player.id is not None
    assert player.nick_norm == "scaryyyyy"
    assert player.nick_display == "Scaryyyyy"
    assert player.discord_id is None
    assert player.referrer_player_id is None
    assert player.is_booster is False
    assert player.created_at == NOW
    assert player.updated_at == NOW


async def test_get_or_create_is_idempotent_by_nick(connection: aiosqlite.Connection) -> None:
    repo = PlayersRepository(connection)

    first = await repo.get_or_create(NormalizedNick("scaryyyyy"), "Scaryyyyy", now=NOW)
    second = await repo.get_or_create(
        NormalizedNick("scaryyyyy"), "different-case-ignored", now=NOW
    )

    assert first.id == second.id
    assert second.nick_display == "Scaryyyyy"  # first write wins, not overwritten
    assert len(await repo.all()) == 1


async def test_get_by_nick_and_get_by_id_agree(connection: aiosqlite.Connection) -> None:
    repo = PlayersRepository(connection)
    created = await repo.get_or_create(NormalizedNick("scaryyyyy"), "Scaryyyyy", now=NOW)

    assert created.id is not None
    by_nick = await repo.get_by_nick(NormalizedNick("scaryyyyy"))
    by_id = await repo.get_by_id(created.id)

    assert by_nick == created
    assert by_id == created


async def test_set_discord_id_binds_and_unbinds(connection: aiosqlite.Connection) -> None:
    repo = PlayersRepository(connection)
    player = await repo.get_or_create(NormalizedNick("scaryyyyy"), "Scaryyyyy", now=NOW)
    assert player.id is not None

    later = datetime(2026, 8, 11, tzinfo=UTC)
    await repo.set_discord_id(player.id, 12345, now=later)
    bound = await repo.get_by_id(player.id)
    assert bound is not None
    assert bound.discord_id == 12345
    assert bound.updated_at == later

    found_by_discord = await repo.get_by_discord_id(12345)
    assert found_by_discord is not None
    assert found_by_discord.id == player.id

    await repo.set_discord_id(player.id, None, now=later)
    unbound = await repo.get_by_id(player.id)
    assert unbound is not None
    assert unbound.discord_id is None


async def test_set_referrer(connection: aiosqlite.Connection) -> None:
    repo = PlayersRepository(connection)
    referrer = await repo.get_or_create(NormalizedNick("referrer"), "Referrer", now=NOW)
    referred = await repo.get_or_create(NormalizedNick("referred"), "Referred", now=NOW)
    assert referrer.id is not None and referred.id is not None

    await repo.set_referrer(referred.id, referrer.id, now=NOW)

    updated = await repo.get_by_id(referred.id)
    assert updated is not None
    assert updated.referrer_player_id == referrer.id


async def test_set_booster(connection: aiosqlite.Connection) -> None:
    repo = PlayersRepository(connection)
    player = await repo.get_or_create(NormalizedNick("scaryyyyy"), "Scaryyyyy", now=NOW)
    assert player.id is not None

    await repo.set_booster(player.id, True, now=NOW)

    updated = await repo.get_by_id(player.id)
    assert updated is not None
    assert updated.is_booster is True


async def test_all_orders_by_id(connection: aiosqlite.Connection) -> None:
    repo = PlayersRepository(connection)
    await repo.get_or_create(NormalizedNick("b"), "B", now=NOW)
    await repo.get_or_create(NormalizedNick("a"), "A", now=NOW)

    players = await repo.all()

    assert [p.nick_norm for p in players] == ["b", "a"]  # insertion order (by id), not alpha


async def test_referrer_cannot_be_self(connection: aiosqlite.Connection) -> None:
    repo = PlayersRepository(connection)
    player = await repo.get_or_create(NormalizedNick("scaryyyyy"), "Scaryyyyy", now=NOW)
    assert player.id is not None

    with pytest.raises(aiosqlite.IntegrityError):
        await repo.set_referrer(player.id, player.id, now=NOW)


async def test_bind_discord_binds_an_unbound_nick(connection: aiosqlite.Connection) -> None:
    repo = PlayersRepository(connection)
    await repo.get_or_create(NormalizedNick("scaryyyyy"), "Scaryyyyy", now=NOW)

    bound = await repo.bind_discord(NormalizedNick("scaryyyyy"), 999, force=False, now=NOW)

    assert bound is True
    player = await repo.get_by_nick(NormalizedNick("scaryyyyy"))
    assert player is not None
    assert player.discord_id == 999


async def test_bind_discord_no_op_for_unknown_nick(connection: aiosqlite.Connection) -> None:
    repo = PlayersRepository(connection)

    bound = await repo.bind_discord(NormalizedNick("nobody"), 999, force=False, now=NOW)

    assert bound is False


async def test_bind_discord_no_op_when_already_bound_to_same_id(
    connection: aiosqlite.Connection,
) -> None:
    repo = PlayersRepository(connection)
    player = await repo.get_or_create(NormalizedNick("scaryyyyy"), "Scaryyyyy", now=NOW)
    assert player.id is not None
    await repo.set_discord_id(player.id, 999, now=NOW)

    bound = await repo.bind_discord(NormalizedNick("scaryyyyy"), 999, force=False, now=NOW)

    assert bound is False


async def test_bind_discord_does_not_rebind_without_force(
    connection: aiosqlite.Connection,
) -> None:
    repo = PlayersRepository(connection)
    player = await repo.get_or_create(NormalizedNick("scaryyyyy"), "Scaryyyyy", now=NOW)
    assert player.id is not None
    await repo.set_discord_id(player.id, 111, now=NOW)

    bound = await repo.bind_discord(NormalizedNick("scaryyyyy"), 999, force=False, now=NOW)

    assert bound is False
    unchanged = await repo.get_by_nick(NormalizedNick("scaryyyyy"))
    assert unchanged is not None
    assert unchanged.discord_id == 111


async def test_bind_discord_rebinds_when_forced(connection: aiosqlite.Connection) -> None:
    repo = PlayersRepository(connection)
    player = await repo.get_or_create(NormalizedNick("scaryyyyy"), "Scaryyyyy", now=NOW)
    assert player.id is not None
    await repo.set_discord_id(player.id, 111, now=NOW)

    bound = await repo.bind_discord(NormalizedNick("scaryyyyy"), 999, force=True, now=NOW)

    assert bound is True
    updated = await repo.get_by_nick(NormalizedNick("scaryyyyy"))
    assert updated is not None
    assert updated.discord_id == 999
