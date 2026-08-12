"""Tests for `stalbot.presentation.cogs.manual.ManualCog` (PLAN.md §10.12).

`ManualGrantService`/`ProgressionService` are mocked; the command callbacks
are invoked directly, bypassing discord.py's own dispatch machinery (same
approach as `test_transactions.py`).
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
from discord import app_commands

from stalbot.application.dto.manual_grant import SetRankResult, SetReferralResult
from stalbot.domain.nick import NormalizedNick
from stalbot.domain.progression.ranks import RankLadder
from stalbot.presentation.cogs.manual import ManualCog
from stalbot.presentation.embeds.factory import EmbedFactory


def _referral_result(**overrides: object) -> SetReferralResult:
    defaults: dict[str, object] = {
        "previous_referrer": None,
        "player_discord_bound": False,
        "referrer_discord_bound": False,
    }
    defaults.update(overrides)
    return SetReferralResult(**defaults)  # type: ignore[arg-type]


def _cog(
    *,
    current_referrer: NormalizedNick | None = None,
    referral_result: SetReferralResult | None = None,
    rank_result: SetRankResult | None = None,
) -> tuple[ManualCog, MagicMock, MagicMock]:
    manual_grants = MagicMock()
    manual_grants.current_referrer = AsyncMock(return_value=current_referrer)
    manual_grants.set_referral = AsyncMock(return_value=referral_result or _referral_result())
    manual_grants.set_rank = AsyncMock(
        return_value=rank_result or SetRankResult(tier=_elite_tier(), granted=True)
    )
    progression = MagicMock()
    progression.sync = AsyncMock(return_value=[])
    cog = ManualCog(manual_grants, progression, EmbedFactory())
    return cog, manual_grants, progression


def _elite_tier() -> Any:
    tier = RankLadder().by_key("elite")
    assert tier is not None
    return tier


def _messageable_channel() -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock()
    return channel


def _interaction(*, channel: MagicMock | None = None) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    interaction.user = MagicMock(id=1, mention="<@1>")
    interaction.channel = channel or _messageable_channel()
    return interaction


def _member(member_id: int = 999, *, roles: list[MagicMock] | None = None) -> MagicMock:
    member = MagicMock(spec=discord.Member)
    member.id = member_id
    member.mention = f"<@{member_id}>"
    member.roles = roles or []
    return member


async def _call_set_referral(
    cog: ManualCog,
    interaction: MagicMock,
    *,
    ник: str = "Scaryyyyy",
    discord_member: MagicMock | None = None,
    ник_пригласившего: str = "OtherNick",
    referrer_discord_member: MagicMock | None = None,
) -> None:
    callback: Any = ManualCog.set_referral.callback
    await callback(
        cog,
        interaction,
        ник,
        discord_member or _member(999),
        ник_пригласившего,
        referrer_discord_member or _member(888),
    )


async def _call_set_rank(
    cog: ManualCog,
    interaction: MagicMock,
    *,
    ник: str = "Scaryyyyy",
    discord_member: MagicMock | None = None,
    rank_key: str = "elite",
) -> None:
    tier = RankLadder().by_key(rank_key)
    assert tier is not None
    choice = app_commands.Choice(name=tier.label, value=tier.key)
    callback: Any = ManualCog.set_rank.callback
    await callback(cog, interaction, ник, discord_member or _member(999), choice)


async def test_set_referral_writes_and_notifies() -> None:
    cog, manual_grants, progression = _cog()
    channel = _messageable_channel()
    interaction = _interaction(channel=channel)

    await _call_set_referral(cog, interaction)

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    manual_grants.set_referral.assert_awaited_once_with("Scaryyyyy", "OtherNick", 999, 888)
    progression.sync.assert_awaited_once()
    interaction.followup.send.assert_awaited_once()
    channel.send.assert_awaited_once()


async def test_set_referral_rejects_referrer_equal_to_self() -> None:
    cog, manual_grants, _progression = _cog()
    interaction = _interaction()

    await _call_set_referral(cog, interaction, ник="Scaryyyyy", ник_пригласившего="  scaryyyyy ")

    manual_grants.set_referral.assert_not_called()
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "Реферал не может быть тем же" in (embed.description or "")


async def test_set_referral_skips_confirmation_when_no_existing_referrer() -> None:
    cog, manual_grants, _progression = _cog(current_referrer=None)
    interaction = _interaction()

    await _call_set_referral(cog, interaction)

    manual_grants.set_referral.assert_awaited_once()


async def test_set_referral_asks_for_confirmation_and_proceeds_when_confirmed() -> None:
    cog, manual_grants, _progression = _cog(current_referrer=NormalizedNick("oldreferrer"))
    interaction = _interaction()
    cog._confirm_overwrite = AsyncMock(return_value=True)  # type: ignore[method-assign]

    await _call_set_referral(cog, interaction, ник_пригласившего="NewReferrer")

    cog._confirm_overwrite.assert_awaited_once()
    manual_grants.set_referral.assert_awaited_once()


async def test_set_referral_aborts_when_overwrite_not_confirmed() -> None:
    cog, manual_grants, progression = _cog(current_referrer=NormalizedNick("oldreferrer"))
    interaction = _interaction()
    cog._confirm_overwrite = AsyncMock(return_value=False)  # type: ignore[method-assign]

    await _call_set_referral(cog, interaction, ник_пригласившего="NewReferrer")

    manual_grants.set_referral.assert_not_called()
    progression.sync.assert_not_called()
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert embed.title == "Отменено"


async def test_set_referral_does_not_confirm_when_referrer_unchanged() -> None:
    cog, manual_grants, _progression = _cog(current_referrer=NormalizedNick("othernick"))
    interaction = _interaction()

    await _call_set_referral(cog, interaction, ник_пригласившего="OtherNick")

    manual_grants.set_referral.assert_awaited_once()


async def test_set_rank_grants_when_member_lacks_the_role() -> None:
    cog, manual_grants, progression = _cog(
        rank_result=SetRankResult(tier=_elite_tier(), granted=True)
    )
    channel = _messageable_channel()
    interaction = _interaction(channel=channel)
    member = _member(999, roles=[])

    await _call_set_rank(cog, interaction, discord_member=member)

    (_nick, _discord_id, _tier), kwargs = manual_grants.set_rank.call_args
    assert kwargs["revoke"] is False
    progression.sync.assert_awaited_once()
    channel.send.assert_awaited_once()


async def test_set_rank_toggles_off_when_member_already_has_the_role() -> None:
    cog, manual_grants, _progression = _cog(
        rank_result=SetRankResult(tier=_elite_tier(), granted=False)
    )
    elite_role = MagicMock(id=_elite_tier().role_id)
    member = _member(999, roles=[elite_role])
    interaction = _interaction()

    await _call_set_rank(cog, interaction, discord_member=member)

    (_nick, _discord_id, _tier), kwargs = manual_grants.set_rank.call_args
    assert kwargs["revoke"] is True
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "снят" in (embed.description or "")
