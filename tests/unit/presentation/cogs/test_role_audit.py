"""Tests for `stalbot.presentation.cogs.role_audit.RoleAuditCog` (заявка 21.08.2026 п.8)."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from stalbot.domain.entities.player import Player
from stalbot.domain.nick import NormalizedNick
from stalbot.domain.progression.ranks import RankLadder
from stalbot.presentation.cogs.role_audit import RoleAuditCog
from stalbot.presentation.embeds.factory import EmbedFactory
from stalbot.presentation.views.paginated_embed import PaginatedEmbedView


def _player() -> Player:
    now = datetime(2026, 8, 26, tzinfo=UTC)
    return Player(
        id=1,
        nick_norm=NormalizedNick("scaryyyyy"),
        nick_display="Scaryyyyy",
        discord_id=111,
        referrer_player_id=None,
        is_booster=False,
        created_at=now,
        updated_at=now,
    )


def _member(member_id: int, role_ids: list[int]) -> MagicMock:
    member = MagicMock(spec=discord.Member)
    member.id = member_id
    member.display_name = f"Member{member_id}"
    member.roles = [MagicMock(id=role_id) for role_id in role_ids]
    return member


def _cog(*, get_by_discord_id: MagicMock | None = None) -> tuple[RoleAuditCog, MagicMock]:
    players = MagicMock()
    players.get_by_discord_id = get_by_discord_id or AsyncMock(return_value=None)
    cog = RoleAuditCog(players, EmbedFactory())
    return cog, players


def _interaction(*, guild: MagicMock | None) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = guild
    interaction.user = MagicMock(spec=discord.Member, id=1)
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock(return_value=MagicMock(spec=discord.Message))
    return interaction


async def _call(cog: RoleAuditCog, interaction: MagicMock) -> None:
    callback: Any = RoleAuditCog.role_audit.callback
    await callback(cog, interaction)


async def test_rejects_outside_a_guild() -> None:
    cog, _players = _cog()
    interaction = _interaction(guild=None)

    await _call(cog, interaction)

    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "сервере" in (embed.description or "")


async def test_reports_a_role_holder_with_no_bound_player() -> None:
    standard_role_id = RankLadder().by_key("standard").role_id  # type: ignore[union-attr]
    guild = MagicMock(spec=discord.Guild)
    guild.members = [_member(555, [standard_role_id])]
    cog, players = _cog(get_by_discord_id=AsyncMock(return_value=None))
    interaction = _interaction(guild=guild)

    await _call(cog, interaction)

    players.get_by_discord_id.assert_awaited_once_with(555)
    embed = interaction.followup.send.call_args.kwargs["embed"]
    field = embed.fields[0]
    assert "Member555" in field.name
    assert "555" in field.name
    assert "Standard" in field.value


async def test_omits_a_role_holder_with_a_bound_player() -> None:
    standard_role_id = RankLadder().by_key("standard").role_id  # type: ignore[union-attr]
    guild = MagicMock(spec=discord.Guild)
    guild.members = [_member(111, [standard_role_id])]
    cog, _players = _cog(get_by_discord_id=AsyncMock(return_value=_player()))
    interaction = _interaction(guild=guild)

    await _call(cog, interaction)

    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "Всё совпадает" in (embed.description or "")


async def test_ignores_members_with_no_tracked_role() -> None:
    guild = MagicMock(spec=discord.Guild)
    guild.members = [_member(222, [999999])]
    cog, players = _cog()
    interaction = _interaction(guild=guild)

    await _call(cog, interaction)

    players.get_by_discord_id.assert_not_called()
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "Проверено участников с ролью: 0" in (embed.description or "")


async def test_paginates_past_the_page_size() -> None:
    standard_role_id = RankLadder().by_key("standard").role_id  # type: ignore[union-attr]
    guild = MagicMock(spec=discord.Guild)
    guild.members = [_member(i, [standard_role_id]) for i in range(1, 25)]
    cog, _players = _cog(get_by_discord_id=AsyncMock(return_value=None))
    interaction = _interaction(guild=guild)

    await _call(cog, interaction)

    kwargs = interaction.followup.send.call_args.kwargs
    assert isinstance(kwargs["view"], PaginatedEmbedView)
