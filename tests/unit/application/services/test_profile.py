"""Tests for `stalbot.application.services.profile.ProfileService` (PLAN.md §10.2, §10.3)."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from stalbot.application.services.profile import ProfileService
from stalbot.domain.entities.player import Player
from stalbot.domain.entities.player_progression import PlayerProgressionRecord
from stalbot.domain.errors import PlayerNotFoundError, ProfileAccessDeniedError
from stalbot.domain.nick import NormalizedNick

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


def _service(
    *,
    player: Player | None,
    record: PlayerProgressionRecord | None = None,
    referred: list[Player] | None = None,
) -> tuple[ProfileService, MagicMock, MagicMock]:
    players = MagicMock()
    players.get_by_nick = AsyncMock(return_value=player)
    players.list_by_referrer = AsyncMock(return_value=referred or [])
    progression = MagicMock()
    progression.get = AsyncMock(return_value=record)
    return ProfileService(players, progression), players, progression


async def test_get_profile_returns_view_for_own_bound_nick() -> None:
    service, _players, _progression_repo = _service(
        player=_player(discord_id=111), record=_progression()
    )

    view = await service.get_profile(
        "Scaryyyyy", requester_id=111, is_admin=False, admin_can_view_any=True
    )

    assert view.player.discord_id == 111
    assert view.nick_display == "Scaryyyyy"
    assert view.coins == 1240
    assert view.xp == 3780
    assert view.rank_key == "elite"
    assert view.referral_role_key == "recruiter"
    assert view.referrals_count == 2


async def test_get_profile_defaults_zero_without_a_progression_record() -> None:
    service, _players, _progression_repo = _service(player=_player(discord_id=111), record=None)

    view = await service.get_profile(
        "Scaryyyyy", requester_id=111, is_admin=False, admin_can_view_any=True
    )

    assert view.coins == 0
    assert view.xp == 0
    assert view.rank_key is None
    assert view.referral_role_key is None
    assert view.referrals_count == 0


async def test_get_profile_raises_when_nick_not_found() -> None:
    service, _players, _progression_repo = _service(player=None)

    with pytest.raises(PlayerNotFoundError):
        await service.get_profile("Ghost", requester_id=1, is_admin=False, admin_can_view_any=True)


async def test_get_profile_denies_non_admin_viewing_someone_elses_profile() -> None:
    service, _players, _progression_repo = _service(player=_player(discord_id=222))

    with pytest.raises(ProfileAccessDeniedError):
        await service.get_profile(
            "Scaryyyyy", requester_id=1, is_admin=False, admin_can_view_any=True
        )


async def test_get_profile_denies_unbound_profile_for_non_admin() -> None:
    service, _players, _progression_repo = _service(player=_player(discord_id=None))

    with pytest.raises(ProfileAccessDeniedError):
        await service.get_profile(
            "Scaryyyyy", requester_id=1, is_admin=False, admin_can_view_any=True
        )


async def test_get_profile_allows_admin_when_flag_enabled() -> None:
    service, _players, _progression_repo = _service(player=_player(discord_id=222))

    view = await service.get_profile(
        "Scaryyyyy", requester_id=1, is_admin=True, admin_can_view_any=True
    )

    assert view.player.discord_id == 222


async def test_get_profile_denies_admin_when_flag_disabled() -> None:
    service, _players, _progression_repo = _service(player=_player(discord_id=222))

    with pytest.raises(ProfileAccessDeniedError):
        await service.get_profile(
            "Scaryyyyy", requester_id=1, is_admin=True, admin_can_view_any=False
        )


async def test_list_referrals_resolves_display_and_discord_id() -> None:
    referred_player = _player(
        id=2, nick_norm=NormalizedNick("alice"), nick_display="Alice", discord_id=999
    )
    service, _players, _progression_repo = _service(
        player=_player(discord_id=111), referred=[referred_player]
    )

    view, referred = await service.list_referrals(
        "Scaryyyyy", requester_id=111, is_admin=False, admin_can_view_any=True
    )

    assert view.nick_display == "Scaryyyyy"
    assert len(referred) == 1
    assert referred[0].nick_display == "Alice"
    assert referred[0].discord_id == 999


async def test_list_referrals_leaves_discord_id_none_when_referred_player_unbound() -> None:
    referred_player = _player(
        id=2, nick_norm=NormalizedNick("ghost"), nick_display="Ghost", discord_id=None
    )
    service, _players, _progression_repo = _service(
        player=_player(discord_id=111), referred=[referred_player]
    )

    _view, referred = await service.list_referrals(
        "Scaryyyyy", requester_id=111, is_admin=False, admin_can_view_any=True
    )

    assert referred[0].discord_id is None


async def test_list_referrals_propagates_access_check() -> None:
    service, _players, _progression_repo = _service(player=_player(discord_id=222))

    with pytest.raises(ProfileAccessDeniedError):
        await service.list_referrals(
            "Scaryyyyy", requester_id=1, is_admin=False, admin_can_view_any=True
        )
