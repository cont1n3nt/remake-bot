"""Tests for `stalbot.application.services.manual_grants.ManualGrantService`.

PLAN.md §10.12; sqlite_migration.md Э7. `RoleGateway` is mocked; the cache
repositories are real, SQLite-backed, for genuine round-trip confidence
(same approach as `test_transaction_service.py`).
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import aiosqlite

from stalbot.application.dto.progression_state import ProgressionState
from stalbot.application.ports.role_gateway import RoleDiff, RoleGateway, RoleSet
from stalbot.application.services.manual_grants import ManualGrantService
from stalbot.domain.nick import NormalizedNick
from stalbot.domain.progression.ranks import RankLadder
from stalbot.infrastructure.cache.repositories.players import PlayersRepository
from stalbot.infrastructure.cache.repositories.progression import ProgressionRepository
from stalbot.infrastructure.cache.repositories.progression_state import ProgressionStateRepository
from tests.support.fake_clock import FakeClock


def _fake_roles() -> MagicMock:
    gateway = MagicMock(spec=RoleGateway)
    gateway.sync_roles = AsyncMock(return_value=RoleDiff(granted=(), revoked=()))
    return gateway


def _service(
    connection: aiosqlite.Connection, *, roles: MagicMock
) -> tuple[ManualGrantService, PlayersRepository, ProgressionStateRepository]:
    players = PlayersRepository(connection)
    progression_state = ProgressionStateRepository(connection)
    service = ManualGrantService(
        players,
        ProgressionRepository(connection),
        progression_state,
        roles,
        clock=FakeClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC)),
    )
    return service, players, progression_state


async def test_current_referrer_is_none_for_an_unknown_player(
    connection: aiosqlite.Connection,
) -> None:
    service, _players, _state = _service(connection, roles=_fake_roles())

    assert await service.current_referrer("Scaryyyyy") is None


async def test_current_referrer_is_none_without_a_referrer(
    connection: aiosqlite.Connection,
) -> None:
    service, players, _state = _service(connection, roles=_fake_roles())
    await players.get_or_create(
        NormalizedNick("scaryyyyy"), "Scaryyyyy", now=datetime(2026, 8, 2, 11, 0, tzinfo=UTC)
    )

    assert await service.current_referrer("Scaryyyyy") is None


async def test_current_referrer_returns_the_players_referrer(
    connection: aiosqlite.Connection,
) -> None:
    service, players, _state = _service(connection, roles=_fake_roles())
    now = datetime(2026, 8, 2, 11, 0, tzinfo=UTC)
    player = await players.get_or_create(NormalizedNick("scaryyyyy"), "Scaryyyyy", now=now)
    referrer = await players.get_or_create(NormalizedNick("othernick"), "OtherNick", now=now)
    assert player.id is not None and referrer.id is not None
    await players.set_referrer(player.id, referrer.id, now=now)

    assert await service.current_referrer("Scaryyyyy") == NormalizedNick("othernick")


async def test_current_discord_id_is_none_for_an_unknown_player(
    connection: aiosqlite.Connection,
) -> None:
    service, _players, _state = _service(connection, roles=_fake_roles())

    assert await service.current_discord_id("Scaryyyyy") is None


async def test_link_discord_creates_the_player_row_and_binds_it(
    connection: aiosqlite.Connection,
) -> None:
    service, players, _state = _service(connection, roles=_fake_roles())

    bound = await service.link_discord("Scaryyyyy", 999)

    assert bound is True
    player = await players.get_by_nick(NormalizedNick("scaryyyyy"))
    assert player is not None
    assert player.discord_id == 999


async def test_link_discord_overwrites_an_existing_different_binding(
    connection: aiosqlite.Connection,
) -> None:
    service, players, _state = _service(connection, roles=_fake_roles())
    now = datetime(2026, 8, 2, 11, 0, tzinfo=UTC)
    player = await players.get_or_create(NormalizedNick("scaryyyyy"), "Scaryyyyy", now=now)
    assert player.id is not None
    await players.set_discord_id(player.id, 111, now=now)

    bound = await service.link_discord("Scaryyyyy", 999)

    assert bound is True
    assert await service.current_discord_id("Scaryyyyy") == 999


async def test_link_discord_is_a_no_op_when_already_bound_to_the_same_account(
    connection: aiosqlite.Connection,
) -> None:
    service, _players, _state = _service(connection, roles=_fake_roles())
    await service.link_discord("Scaryyyyy", 999)

    bound = await service.link_discord("Scaryyyyy", 999)

    assert bound is False


async def test_set_referral_creates_both_players_and_sets_the_referrer(
    connection: aiosqlite.Connection,
) -> None:
    service, players, _state = _service(connection, roles=_fake_roles())

    result = await service.set_referral("Scaryyyyy", "OtherNick", 1, 2)

    assert result.previous_referrer is None
    player = await players.get_by_nick(NormalizedNick("scaryyyyy"))
    referrer = await players.get_by_nick(NormalizedNick("othernick"))
    assert player is not None and referrer is not None
    assert player.referrer_player_id == referrer.id


async def test_set_referral_reports_the_previous_referrer(
    connection: aiosqlite.Connection,
) -> None:
    service, players, _state = _service(connection, roles=_fake_roles())
    now = datetime(2026, 8, 2, 11, 0, tzinfo=UTC)
    player = await players.get_or_create(NormalizedNick("scaryyyyy"), "Scaryyyyy", now=now)
    old_referrer = await players.get_or_create(
        NormalizedNick("oldreferrer"), "OldReferrer", now=now
    )
    assert player.id is not None and old_referrer.id is not None
    await players.set_referrer(player.id, old_referrer.id, now=now)

    result = await service.set_referral("Scaryyyyy", "NewReferrer", 1, 2)

    assert result.previous_referrer == NormalizedNick("oldreferrer")


async def test_set_referral_recomputes_progression_for_both_players(
    connection: aiosqlite.Connection,
) -> None:
    service, players, _state = _service(connection, roles=_fake_roles())
    progression = ProgressionRepository(connection)

    await service.set_referral("Scaryyyyy", "OtherNick", 1, 2)

    player = await players.get_by_nick(NormalizedNick("scaryyyyy"))
    referrer = await players.get_by_nick(NormalizedNick("othernick"))
    assert player is not None and referrer is not None
    assert await progression.get(player.id) is not None  # type: ignore[arg-type]
    assert await progression.get(referrer.id) is not None  # type: ignore[arg-type]


async def test_set_referral_binds_both_discord_ids(connection: aiosqlite.Connection) -> None:
    service, players, _state = _service(connection, roles=_fake_roles())

    result = await service.set_referral("Scaryyyyy", "OtherNick", 111, 222)

    assert result.player_discord_bound is True
    assert result.referrer_discord_bound is True
    player = await players.get_by_nick(NormalizedNick("scaryyyyy"))
    referrer = await players.get_by_nick(NormalizedNick("othernick"))
    assert player is not None and player.discord_id == 111
    assert referrer is not None and referrer.discord_id == 222


async def test_set_rank_grants_the_role_and_sets_manual_flag(
    connection: aiosqlite.Connection,
) -> None:
    roles = _fake_roles()
    service, _players, state = _service(connection, roles=roles)
    tier = RankLadder().by_key("elite")
    assert tier is not None

    result = await service.set_rank("Scaryyyyy", 999, tier, revoke=False)

    assert result.granted is True
    roles.sync_roles.assert_awaited_once_with(
        999, RoleSet(desired=frozenset({tier.role_id}), universe=RankLadder().role_ids)
    )
    stored = await state.get(NormalizedNick("scaryyyyy"))
    assert stored is not None
    assert stored.manual_rank_role is True


async def test_set_rank_toggle_off_revokes_and_clears_the_flag(
    connection: aiosqlite.Connection,
) -> None:
    roles = _fake_roles()
    service, _players, state = _service(connection, roles=roles)
    tier = RankLadder().by_key("elite")
    assert tier is not None
    await state.upsert(
        ProgressionState(
            nick=NormalizedNick("scaryyyyy"),
            last_rank="premium",
            last_referral_role="scout",
            manual_rank_role=True,
            announced_at=None,
        )
    )

    result = await service.set_rank("Scaryyyyy", 999, tier, revoke=True)

    assert result.granted is False
    roles.sync_roles.assert_awaited_once_with(
        999, RoleSet(desired=frozenset(), universe=RankLadder().role_ids)
    )
    stored = await state.get(NormalizedNick("scaryyyyy"))
    assert stored is not None
    assert stored.manual_rank_role is False
    # Tracked rank/referral-role keys survive the toggle.
    assert stored.last_rank == "premium"
    assert stored.last_referral_role == "scout"
