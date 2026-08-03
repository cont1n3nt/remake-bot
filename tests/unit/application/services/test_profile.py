"""Tests for `stalbot.application.services.profile.ProfileService` (PLAN.md §10.2, §10.3)."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from stalbot.application.services.profile import ProfileService
from stalbot.domain.entities.user_profile import UserProfile
from stalbot.domain.errors import PlayerNotFoundError, ProfileAccessDeniedError
from stalbot.domain.nick import NormalizedNick


def _profile(**overrides: object) -> UserProfile:
    defaults: dict[str, object] = {
        "row": 3,
        "nick": NormalizedNick("scaryyyyy"),
        "discord_id": 111,
        "coins": 1240,
        "xp": 3780,
        "buy_turnover": Decimal(0),
        "sell_turnover": Decimal(0),
        "total_turnover": Decimal(0),
        "referrals_count": 2,
        "is_booster": False,
        "rank": "💎 Elite",
        "referral_role": "🧲 Вербовщик",
    }
    defaults.update(overrides)
    return UserProfile(**defaults)  # type: ignore[arg-type]


def _service(
    *, profile: UserProfile | None, referral_targets: list[NormalizedNick] | None = None
) -> tuple[ProfileService, MagicMock, MagicMock]:
    users = MagicMock()
    users.get_by_nick = AsyncMock(return_value=profile)
    users.get_nick_display = AsyncMock(return_value="Scaryyyyy")
    transactions = MagicMock()
    transactions.list_referral_targets = AsyncMock(return_value=referral_targets or [])
    return ProfileService(users, transactions), users, transactions


async def test_get_profile_returns_view_for_own_bound_nick() -> None:
    service, _users, _tx = _service(profile=_profile(discord_id=111))

    view = await service.get_profile(
        "Scaryyyyy", requester_id=111, is_admin=False, admin_can_view_any=True
    )

    assert view.profile.discord_id == 111
    assert view.nick_display == "Scaryyyyy"


async def test_get_profile_raises_when_nick_not_found() -> None:
    service, _users, _tx = _service(profile=None)

    with pytest.raises(PlayerNotFoundError):
        await service.get_profile("Ghost", requester_id=1, is_admin=False, admin_can_view_any=True)


async def test_get_profile_denies_non_admin_viewing_someone_elses_profile() -> None:
    service, _users, _tx = _service(profile=_profile(discord_id=222))

    with pytest.raises(ProfileAccessDeniedError):
        await service.get_profile(
            "Scaryyyyy", requester_id=1, is_admin=False, admin_can_view_any=True
        )


async def test_get_profile_denies_unbound_profile_for_non_admin() -> None:
    service, _users, _tx = _service(profile=_profile(discord_id=None))

    with pytest.raises(ProfileAccessDeniedError):
        await service.get_profile(
            "Scaryyyyy", requester_id=1, is_admin=False, admin_can_view_any=True
        )


async def test_get_profile_allows_admin_when_flag_enabled() -> None:
    service, _users, _tx = _service(profile=_profile(discord_id=222))

    view = await service.get_profile(
        "Scaryyyyy", requester_id=1, is_admin=True, admin_can_view_any=True
    )

    assert view.profile.discord_id == 222


async def test_get_profile_denies_admin_when_flag_disabled() -> None:
    service, _users, _tx = _service(profile=_profile(discord_id=222))

    with pytest.raises(ProfileAccessDeniedError):
        await service.get_profile(
            "Scaryyyyy", requester_id=1, is_admin=True, admin_can_view_any=False
        )


async def test_list_referrals_resolves_display_and_discord_id() -> None:
    referred_profile = _profile(nick=NormalizedNick("alice"), discord_id=999)

    users = MagicMock()
    users.get_by_nick = AsyncMock(
        side_effect=lambda nick: (
            _profile(discord_id=111) if nick == "scaryyyyy" else referred_profile
        )
    )
    users.get_nick_display = AsyncMock(
        side_effect=lambda nick: "Scaryyyyy" if nick == "scaryyyyy" else "Alice"
    )
    transactions = MagicMock()
    transactions.list_referral_targets = AsyncMock(return_value=[NormalizedNick("alice")])
    service = ProfileService(users, transactions)

    view, referred = await service.list_referrals(
        "Scaryyyyy", requester_id=111, is_admin=False, admin_can_view_any=True
    )

    assert view.nick_display == "Scaryyyyy"
    assert len(referred) == 1
    assert referred[0].nick_display == "Alice"
    assert referred[0].discord_id == 999


async def test_list_referrals_leaves_discord_id_none_when_referred_profile_missing() -> None:
    users = MagicMock()
    users.get_by_nick = AsyncMock(
        side_effect=lambda nick: _profile(discord_id=111) if nick == "scaryyyyy" else None
    )
    users.get_nick_display = AsyncMock(return_value="Ghost")
    transactions = MagicMock()
    transactions.list_referral_targets = AsyncMock(return_value=[NormalizedNick("ghost")])
    service = ProfileService(users, transactions)

    _view, referred = await service.list_referrals(
        "Scaryyyyy", requester_id=111, is_admin=False, admin_can_view_any=True
    )

    assert referred[0].discord_id is None


async def test_list_referrals_propagates_access_check() -> None:
    service, _users, _tx = _service(profile=_profile(discord_id=222))

    with pytest.raises(ProfileAccessDeniedError):
        await service.list_referrals(
            "Scaryyyyy", requester_id=1, is_admin=False, admin_can_view_any=True
        )
