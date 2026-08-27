"""Tests for `stalbot.presentation.cogs.role_audit.RoleAuditCog` (заявка 21.08.2026 п.8)."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from stalbot.application.dto.role_change import RoleChange
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


def _cog(
    *, get_by_discord_id: MagicMock | None = None, resync_all: MagicMock | None = None
) -> tuple[RoleAuditCog, MagicMock, MagicMock]:
    players = MagicMock()
    players.get_by_discord_id = get_by_discord_id or AsyncMock(return_value=None)
    progression = MagicMock()
    progression.resync_all = resync_all or AsyncMock(return_value=[])
    cog = RoleAuditCog(players, EmbedFactory(), progression)
    return cog, players, progression


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


async def _call_resync(cog: RoleAuditCog, interaction: MagicMock) -> None:
    callback: Any = RoleAuditCog.resync_roles.callback
    await callback(cog, interaction)


async def test_rejects_outside_a_guild() -> None:
    cog, _players, _progression = _cog()
    interaction = _interaction(guild=None)

    await _call(cog, interaction)

    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "сервере" in (embed.description or "")


async def test_reports_a_role_holder_with_no_bound_player() -> None:
    standard_role_id = RankLadder().by_key("standard").role_id  # type: ignore[union-attr]
    guild = MagicMock(spec=discord.Guild)
    guild.members = [_member(555, [standard_role_id])]
    cog, players, _progression = _cog(get_by_discord_id=AsyncMock(return_value=None))
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
    cog, _players, _progression = _cog(get_by_discord_id=AsyncMock(return_value=_player()))
    interaction = _interaction(guild=guild)

    await _call(cog, interaction)

    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "Всё совпадает" in (embed.description or "")


async def test_ignores_members_with_no_tracked_role() -> None:
    guild = MagicMock(spec=discord.Guild)
    guild.members = [_member(222, [999999])]
    cog, players, _progression = _cog()
    interaction = _interaction(guild=guild)

    await _call(cog, interaction)

    players.get_by_discord_id.assert_not_called()
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "Проверено участников с ролью: 0" in (embed.description or "")


async def test_paginates_past_the_page_size() -> None:
    standard_role_id = RankLadder().by_key("standard").role_id  # type: ignore[union-attr]
    guild = MagicMock(spec=discord.Guild)
    guild.members = [_member(i, [standard_role_id]) for i in range(1, 25)]
    cog, _players, _progression = _cog(get_by_discord_id=AsyncMock(return_value=None))
    interaction = _interaction(guild=guild)

    await _call(cog, interaction)

    kwargs = interaction.followup.send.call_args.kwargs
    assert isinstance(kwargs["view"], PaginatedEmbedView)


# -- /resync_roles (заявка 27.08.2026: on-demand full resync) --------------


async def test_resync_reports_when_nothing_changed() -> None:
    cog, _players, progression = _cog(resync_all=AsyncMock(return_value=[]))
    interaction = _interaction(guild=MagicMock(spec=discord.Guild))

    await _call_resync(cog, interaction)

    progression.resync_all.assert_awaited_once()
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "в порядке" in (embed.description or "")


async def test_resync_reports_granted_and_revoked_roles() -> None:
    standard = RankLadder().by_key("standard")
    elite = RankLadder().by_key("elite")
    assert standard is not None and elite is not None
    change = RoleChange(
        nick=NormalizedNick("scaryyyyy"),
        discord_id=111,
        granted=(elite.role_id,),
        revoked=(standard.role_id,),
    )
    cog, _players, _progression = _cog(resync_all=AsyncMock(return_value=[change]))
    interaction = _interaction(guild=MagicMock(spec=discord.Guild))

    await _call_resync(cog, interaction)

    embed = interaction.followup.send.call_args.kwargs["embed"]
    field = embed.fields[0]
    assert "scaryyyyy" in field.name
    assert "<@111>" in field.name
    assert "Elite" in field.value
    assert "Standard" in field.value


async def test_resync_paginates_past_the_page_size() -> None:
    changes = [
        RoleChange(nick=NormalizedNick(f"player{i}"), discord_id=i, granted=(1,), revoked=())
        for i in range(1, 25)
    ]
    cog, _players, _progression = _cog(resync_all=AsyncMock(return_value=changes))
    interaction = _interaction(guild=MagicMock(spec=discord.Guild))

    await _call_resync(cog, interaction)

    kwargs = interaction.followup.send.call_args.kwargs
    assert isinstance(kwargs["view"], PaginatedEmbedView)
