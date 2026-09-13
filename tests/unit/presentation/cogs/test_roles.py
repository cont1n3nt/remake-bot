"""Tests for `stalbot.presentation.cogs.roles.RolesCog` (заявка 27.08.2026).

`/role_audit` and its five tests were removed here along with the command
itself (заявка 13.09.2026 п.11) — what it reported, a role holder with no
player row, is prevented at the source now: `/unlink_discord` strips the
roles as part of unbinding (`tests/unit/presentation/cogs/test_manual.py`).
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from stalbot.application.dto.role_change import RoleChange
from stalbot.domain.nick import NormalizedNick
from stalbot.domain.progression.ranks import RankLadder
from stalbot.presentation.cogs.roles import RolesCog
from stalbot.presentation.embeds.factory import EmbedFactory
from stalbot.presentation.views.paginated_embed import PaginatedEmbedView


def _cog(*, resync_all: MagicMock | None = None) -> tuple[RolesCog, MagicMock]:
    progression = MagicMock()
    progression.resync_all = resync_all or AsyncMock(return_value=[])
    return RolesCog(EmbedFactory(), progression), progression


def _interaction() -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = MagicMock(spec=discord.Guild)
    interaction.user = MagicMock(spec=discord.Member, id=1)
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock(return_value=MagicMock(spec=discord.Message))
    return interaction


async def _call_resync(cog: RolesCog, interaction: MagicMock) -> None:
    callback: Any = RolesCog.resync_roles.callback
    await callback(cog, interaction)


async def test_resync_reports_when_nothing_changed() -> None:
    cog, progression = _cog(resync_all=AsyncMock(return_value=[]))
    interaction = _interaction()

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
    cog, _progression = _cog(resync_all=AsyncMock(return_value=[change]))
    interaction = _interaction()

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
    cog, _progression = _cog(resync_all=AsyncMock(return_value=changes))
    interaction = _interaction()

    await _call_resync(cog, interaction)

    kwargs = interaction.followup.send.call_args.kwargs
    assert isinstance(kwargs["view"], PaginatedEmbedView)
