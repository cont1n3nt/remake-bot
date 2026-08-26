"""Tests for `stalbot.presentation.cogs.profile.ProfileCog` (PLAN.md §10.2, §10.3).

`ProfileService` is mocked — its own behavior (the binding check) is covered
in `tests/unit/application/services/test_profile.py`. This file is about
whether the cog builds the right embeds and pages the referral list.
"""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from stalbot.application.dto.profile_view import ProfileView, ReferredPlayer
from stalbot.domain.entities.player import Player
from stalbot.domain.entities.player_progression import PlayerProgressionRecord
from stalbot.domain.money import format_amount
from stalbot.domain.nick import NormalizedNick
from stalbot.presentation.cogs.profile import ProfileCog
from stalbot.presentation.embeds.factory import EmbedFactory
from stalbot.presentation.views.paginated_embed import PaginatedEmbedView

_NOW = datetime(2026, 8, 10, tzinfo=UTC)


def _player(**overrides: object) -> Player:
    defaults: dict[str, object] = {
        "id": 1,
        "nick_norm": NormalizedNick("scaryyyyy"),
        "nick_display": "Scaryyyyy",
        "discord_id": 111,
        "referrer_player_id": None,
        "is_booster": False,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    defaults.update(overrides)
    return Player(**defaults)  # type: ignore[arg-type]


def _progression(**overrides: object) -> PlayerProgressionRecord:
    defaults: dict[str, object] = {
        "player_id": 1,
        "purchase_turnover": 0,
        "sale_turnover": 0,
        "total_turnover": 0,
        "referral_count": 2,
        "coins": 1240,
        "xp": 3780,
        "rank_key": "elite",
        "referral_role_key": "recruiter",
        "breakdown_json": "{}",
        "calculator_version": 1,
        "computed_at": _NOW,
    }
    defaults.update(overrides)
    return PlayerProgressionRecord(**defaults)  # type: ignore[arg-type]


def _view(**overrides: object) -> ProfileView:
    player = overrides.pop("player", None) or _player()
    progression = overrides.pop("progression", _progression())
    nick_display = str(overrides.pop("nick_display", "Scaryyyyy"))
    return ProfileView(player=player, progression=progression, nick_display=nick_display)  # type: ignore[arg-type]


def _cog(
    *,
    profile_view: ProfileView | None = None,
    referrals: tuple[ProfileView, list[ReferredPlayer]] | None = None,
    settings: MagicMock | None = None,
) -> tuple[ProfileCog, MagicMock]:
    service = MagicMock()
    service.get_profile = AsyncMock(return_value=profile_view or _view())
    service.list_referrals = AsyncMock(return_value=referrals or (_view(), []))
    settings = settings or MagicMock(admin_can_view_any_profile=True)
    cog = ProfileCog(service, EmbedFactory(), settings)
    return cog, service


def _interaction(*, user: MagicMock | None = None) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock(return_value=MagicMock(spec=discord.Message))
    interaction.user = user or _non_admin_member()
    return interaction


def _non_admin_member(member_id: int = 1) -> MagicMock:
    member = MagicMock(spec=discord.Member)
    member.id = member_id
    member.guild_permissions = MagicMock(administrator=False)
    return member


def _admin_member(member_id: int = 1) -> MagicMock:
    member = MagicMock(spec=discord.Member)
    member.id = member_id
    member.guild_permissions = MagicMock(administrator=True)
    return member


async def _call_profile(cog: ProfileCog, interaction: MagicMock, *, ник: str = "Scaryyyyy") -> None:
    callback: Any = ProfileCog.profile.callback
    await callback(cog, interaction, ник)


async def _call_referrals(
    cog: ProfileCog, interaction: MagicMock, *, ник: str = "Scaryyyyy"
) -> None:
    callback: Any = ProfileCog.referrals.callback
    await callback(cog, interaction, ник)


async def test_profile_sends_embed_and_forwards_admin_flags() -> None:
    admin = _admin_member()
    cog, service = _cog()
    interaction = _interaction(user=admin)

    await _call_profile(cog, interaction, ник="Scaryyyyy")

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    service.get_profile.assert_awaited_once_with(
        "Scaryyyyy", requester_id=admin.id, is_admin=True, admin_can_view_any=True
    )
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "Scaryyyyy" in (embed.title or "")
    assert "🎁 Бонусы ранга" in (embed.description or "")
    field_values = {field.name: field.value for field in embed.fields}
    assert field_values["🪙 Coins"] == "1 240"
    assert field_values["⚡ XP"] == "3 780"
    assert field_values["🏅 Ранг"] == "💎 Elite"
    assert field_values["🤝 Реф-роль"] == "🧲 Вербовщик"
    assert field_values["👥 Приглашено"] == "2"


async def test_profile_shows_max_rank_notice_when_maxed() -> None:
    maxed = _progression(xp=999_999, rank_key="legend")
    cog, _service = _cog(profile_view=_view(progression=maxed))
    interaction = _interaction()

    await _call_profile(cog, interaction)

    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "Достигнут максимальный уровень" in (embed.description or "")


async def test_profile_omits_rank_bonuses_when_no_rank_yet() -> None:
    unranked = _progression(xp=10, rank_key=None, referral_role_key=None)
    cog, _service = _cog(profile_view=_view(progression=unranked))
    interaction = _interaction()

    await _call_profile(cog, interaction)

    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "🎁 Бонусы ранга" not in (embed.description or "")
    assert "📈 До 🔹 Standard" in (embed.description or "")


async def test_profile_omits_referral_role_field_when_unset() -> None:
    view = _view(progression=_progression(referral_role_key=None))
    cog, _service = _cog(profile_view=view)
    interaction = _interaction()

    await _call_profile(cog, interaction)

    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "🤝 Реф-роль" not in {field.name for field in embed.fields}


async def test_profile_omits_discord_field_when_unbound() -> None:
    view = _view(player=_player(discord_id=None))
    cog, _service = _cog(profile_view=view)
    interaction = _interaction()

    await _call_profile(cog, interaction)

    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "💬 Discord" not in {field.name for field in embed.fields}


async def test_profile_shows_discord_field_when_bound() -> None:
    view = _view(player=_player(discord_id=777))
    cog, _service = _cog(profile_view=view)
    interaction = _interaction()

    await _call_profile(cog, interaction)

    embed = interaction.followup.send.call_args.kwargs["embed"]
    field_values = {field.name: field.value for field in embed.fields}
    assert field_values["💬 Discord"] == "<@777>"


async def test_profile_omits_turnover_fields_when_all_zero() -> None:
    cog, _service = _cog()  # default progression has every turnover at 0
    interaction = _interaction()

    await _call_profile(cog, interaction)

    embed = interaction.followup.send.call_args.kwargs["embed"]
    field_names = {field.name for field in embed.fields}
    assert "📤 Оборот продаж" not in field_names
    assert "📥 Оборот покупок" not in field_names
    assert "💹 Общий оборот" not in field_names


async def test_profile_shows_nonzero_turnover_fields_only() -> None:
    view = _view(
        progression=_progression(purchase_turnover=500_000, sale_turnover=0, total_turnover=500_000)
    )
    cog, _service = _cog(profile_view=view)
    interaction = _interaction()

    await _call_profile(cog, interaction)

    embed = interaction.followup.send.call_args.kwargs["embed"]
    field_values = {field.name: field.value for field in embed.fields}
    assert field_values["📤 Оборот продаж"] == format_amount(500_000)
    assert "📥 Оборот покупок" not in field_values
    assert field_values["💹 Общий оборот"] == format_amount(500_000)


async def test_referrals_single_page_sends_without_pager_view() -> None:
    referred = [ReferredPlayer(nick_display="Alice", discord_id=999)]
    cog, service = _cog(referrals=(_view(), referred))
    interaction = _interaction()

    await _call_referrals(cog, interaction)

    service.list_referrals.assert_awaited_once()
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "👥 Приглашено (1)" in (embed.description or "")
    assert "Alice → <@999>" in (embed.description or "")
    assert "view" not in interaction.followup.send.call_args.kwargs


async def test_referrals_empty_list_shows_placeholder_line() -> None:
    cog, _service = _cog(referrals=(_view(), []))
    interaction = _interaction()

    await _call_referrals(cog, interaction)

    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "Пока никого не пригласил(а)." in (embed.description or "")


async def test_referrals_paginates_when_more_than_page_size() -> None:
    referred = [ReferredPlayer(nick_display=f"Player{i}", discord_id=1000 + i) for i in range(20)]
    cog, _service = _cog(referrals=(_view(), referred))
    interaction = _interaction()

    await _call_referrals(cog, interaction)

    kwargs = interaction.followup.send.call_args.kwargs
    assert isinstance(kwargs["view"], PaginatedEmbedView)
    assert "стр. 1/2" in (kwargs["embed"].title or "")


async def test_referrals_paginates_100_referrals_within_embed_limits() -> None:
    """PLAN.md §15 M11 DoD: embed limits verified at 100 referrals."""
    referred = [ReferredPlayer(nick_display=f"Player{i}", discord_id=1000 + i) for i in range(100)]
    cog, _service = _cog(referrals=(_view(), referred))
    interaction = _interaction()

    await _call_referrals(cog, interaction)

    kwargs = interaction.followup.send.call_args.kwargs
    view = kwargs["view"]
    assert isinstance(view, PaginatedEmbedView)
    assert "стр. 1/7" in (kwargs["embed"].title or "")  # ceil(100 / 15)
    for page in view._pages:
        assert len(page) <= 6000
        assert len(page.description or "") <= 4096
