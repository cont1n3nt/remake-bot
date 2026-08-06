"""Tests for `stalbot.infrastructure.discord.role_gateway.DiscordRoleGateway`."""

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from stalbot.application.ports.role_gateway import RoleSet
from stalbot.infrastructure.discord.role_gateway import DiscordRoleGateway

_UNIVERSE = frozenset({1, 2, 3})


def _member(*, current_role_ids: frozenset[int]) -> MagicMock:
    member = MagicMock(spec=discord.Member)
    member.roles = [MagicMock(id=role_id) for role_id in current_role_ids]
    member.add_roles = AsyncMock()
    member.remove_roles = AsyncMock()
    return member


def _client_with_guild(guild: MagicMock | None) -> MagicMock:
    client = MagicMock(spec=discord.Client)
    client.get_guild.return_value = guild
    return client


async def test_grants_the_desired_role_when_member_has_none() -> None:
    member = _member(current_role_ids=frozenset())
    guild = MagicMock(spec=discord.Guild)
    guild.get_member.return_value = member
    client = _client_with_guild(guild)

    gateway = DiscordRoleGateway(client, guild_id=999)
    diff = await gateway.sync_roles(42, RoleSet(desired=frozenset({1}), universe=_UNIVERSE))

    assert diff.granted == (1,)
    assert diff.revoked == ()
    member.add_roles.assert_awaited_once()
    member.remove_roles.assert_not_called()


async def test_revokes_the_previous_ladder_role_when_switching_tiers() -> None:
    member = _member(current_role_ids=frozenset({1}))
    guild = MagicMock(spec=discord.Guild)
    guild.get_member.return_value = member
    client = _client_with_guild(guild)

    gateway = DiscordRoleGateway(client, guild_id=999)
    diff = await gateway.sync_roles(42, RoleSet(desired=frozenset({2}), universe=_UNIVERSE))

    assert diff.granted == (2,)
    assert diff.revoked == (1,)
    member.add_roles.assert_awaited_once()
    member.remove_roles.assert_awaited_once()


async def test_never_touches_roles_outside_the_universe() -> None:
    """A role the member holds that isn't part of this ladder must survive untouched."""
    member = _member(current_role_ids=frozenset({99}))  # outside _UNIVERSE entirely
    guild = MagicMock(spec=discord.Guild)
    guild.get_member.return_value = member
    client = _client_with_guild(guild)

    gateway = DiscordRoleGateway(client, guild_id=999)
    diff = await gateway.sync_roles(42, RoleSet(desired=frozenset({1}), universe=_UNIVERSE))

    assert diff.revoked == ()  # 99 was never in the universe, so it's left alone
    member.remove_roles.assert_not_called()


async def test_is_a_no_op_when_member_already_matches() -> None:
    member = _member(current_role_ids=frozenset({1}))
    guild = MagicMock(spec=discord.Guild)
    guild.get_member.return_value = member
    client = _client_with_guild(guild)

    gateway = DiscordRoleGateway(client, guild_id=999)
    diff = await gateway.sync_roles(42, RoleSet(desired=frozenset({1}), universe=_UNIVERSE))

    assert diff == diff.__class__(granted=(), revoked=())
    member.add_roles.assert_not_called()
    member.remove_roles.assert_not_called()


async def test_falls_back_to_fetch_member_when_not_cached() -> None:
    member = _member(current_role_ids=frozenset())
    guild = MagicMock(spec=discord.Guild)
    guild.get_member.return_value = None
    guild.fetch_member = AsyncMock(return_value=member)
    client = _client_with_guild(guild)

    gateway = DiscordRoleGateway(client, guild_id=999)
    await gateway.sync_roles(42, RoleSet(desired=frozenset({1}), universe=_UNIVERSE))

    guild.fetch_member.assert_awaited_once_with(42)


async def test_is_a_no_op_when_guild_is_not_visible() -> None:
    client = _client_with_guild(None)

    gateway = DiscordRoleGateway(client, guild_id=999)
    diff = await gateway.sync_roles(42, RoleSet(desired=frozenset({1}), universe=_UNIVERSE))

    assert diff.granted == ()
    assert diff.revoked == ()


async def test_is_a_no_op_when_member_not_found() -> None:
    guild = MagicMock(spec=discord.Guild)
    guild.get_member.return_value = None
    guild.fetch_member = AsyncMock(side_effect=discord.NotFound(MagicMock(), "not found"))
    client = _client_with_guild(guild)

    gateway = DiscordRoleGateway(client, guild_id=999)
    diff = await gateway.sync_roles(42, RoleSet(desired=frozenset({1}), universe=_UNIVERSE))

    assert diff.granted == ()
    assert diff.revoked == ()


async def test_is_a_no_op_when_bot_lacks_permission() -> None:
    member = _member(current_role_ids=frozenset())
    member.add_roles = AsyncMock(side_effect=discord.Forbidden(MagicMock(), "missing permissions"))
    guild = MagicMock(spec=discord.Guild)
    guild.get_member.return_value = member
    client = _client_with_guild(guild)

    gateway = DiscordRoleGateway(client, guild_id=999)
    diff = await gateway.sync_roles(42, RoleSet(desired=frozenset({1}), universe=_UNIVERSE))

    assert diff.granted == ()
    assert diff.revoked == ()


async def test_does_not_crash_the_poller_on_a_non_forbidden_http_exception() -> None:
    """INFRA2-1: a transient 5xx or a deleted role id raises `discord.HTTPException`,
    not `discord.Forbidden` — letting it escape would kill the whole background
    poll over every remaining player, not just this one member's sync."""
    member = _member(current_role_ids=frozenset())
    member.add_roles = AsyncMock(
        side_effect=discord.HTTPException(MagicMock(), "internal server error")
    )
    guild = MagicMock(spec=discord.Guild)
    guild.get_member.return_value = member
    client = _client_with_guild(guild)

    gateway = DiscordRoleGateway(client, guild_id=999)
    diff = await gateway.sync_roles(42, RoleSet(desired=frozenset({1}), universe=_UNIVERSE))

    assert diff.granted == ()
    assert diff.revoked == ()


async def test_a_failed_revoke_does_not_hide_a_successful_grant() -> None:
    """INFRA2-2: grant and revoke are independent — if grant succeeds and
    revoke then fails, the member really does hold both roles now, and the
    returned diff must say so instead of reporting "nothing changed"."""
    member = _member(current_role_ids=frozenset({1}))
    member.remove_roles = AsyncMock(
        side_effect=discord.HTTPException(MagicMock(), "internal server error")
    )
    guild = MagicMock(spec=discord.Guild)
    guild.get_member.return_value = member
    client = _client_with_guild(guild)

    gateway = DiscordRoleGateway(client, guild_id=999)
    diff = await gateway.sync_roles(42, RoleSet(desired=frozenset({2}), universe=_UNIVERSE))

    assert diff.granted == (2,)  # the grant that actually happened is reported
    assert diff.revoked == ()  # the failed revoke correctly reports nothing changed
    member.add_roles.assert_awaited_once()
    member.remove_roles.assert_awaited_once()


async def test_a_failed_grant_does_not_prevent_the_revoke_from_being_attempted() -> None:
    member = _member(current_role_ids=frozenset({1}))
    member.add_roles = AsyncMock(
        side_effect=discord.HTTPException(MagicMock(), "internal server error")
    )
    guild = MagicMock(spec=discord.Guild)
    guild.get_member.return_value = member
    client = _client_with_guild(guild)

    gateway = DiscordRoleGateway(client, guild_id=999)
    diff = await gateway.sync_roles(42, RoleSet(desired=frozenset({2}), universe=_UNIVERSE))

    assert diff.granted == ()
    assert diff.revoked == (1,)  # the revoke still runs and still succeeds
    member.remove_roles.assert_awaited_once()


def test_role_set_is_frozen() -> None:
    role_set = RoleSet(desired=frozenset({1}), universe=_UNIVERSE)
    with pytest.raises(AttributeError):
        role_set.desired = frozenset()  # type: ignore[misc]
