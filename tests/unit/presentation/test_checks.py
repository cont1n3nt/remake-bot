"""Tests for `stalbot.presentation.checks` (PLAN.md §5.5)."""

from unittest.mock import MagicMock

import discord

from stalbot.presentation.checks import _is_administrator


def _interaction_with_user(user: object) -> discord.Interaction:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = user
    return interaction


async def test_admin_member_is_allowed() -> None:
    member = MagicMock(spec=discord.Member)
    member.guild_permissions = discord.Permissions(administrator=True)
    interaction = _interaction_with_user(member)

    assert await _is_administrator(interaction) is True


async def test_non_admin_member_is_denied() -> None:
    member = MagicMock(spec=discord.Member)
    member.guild_permissions = discord.Permissions(administrator=False)
    interaction = _interaction_with_user(member)

    assert await _is_administrator(interaction) is False


async def test_non_member_user_is_denied() -> None:
    user = MagicMock(spec=discord.User)
    interaction = _interaction_with_user(user)

    assert await _is_administrator(interaction) is False
