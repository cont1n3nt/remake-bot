"""Tests for `UsersCacheRepository` against a real (temp-file) SQLite connection."""

from datetime import datetime
from decimal import Decimal

import aiosqlite

from stalbot.domain.entities.user_profile import UserProfile
from stalbot.domain.nick import NormalizedNick
from stalbot.infrastructure.cache.repositories.users import UsersCacheRepository


def _profile(nick: str = "dizzikss", **overrides: object) -> UserProfile:
    defaults: dict[str, object] = {
        "row": 3,
        "nick": NormalizedNick(nick),
        "discord_id": None,
        "coins": 1,
        "xp": 0,
        "buy_turnover": Decimal(0),
        "sell_turnover": Decimal(1225100),
        "total_turnover": Decimal(1225100),
        "referrals_count": 1,
        "is_booster": False,
        "rank": None,
        "referral_role": "🧭 Скаут",
    }
    defaults.update(overrides)
    return UserProfile(**defaults)  # type: ignore[arg-type]


async def test_replace_all_then_get_by_nick(
    connection: aiosqlite.Connection, synced_at: str
) -> None:
    repo = UsersCacheRepository(connection)
    await repo.replace_all(
        [_profile()], nick_displays={NormalizedNick("dizzikss"): "dizzikss"}, synced_at=synced_at
    )

    profile = await repo.get_by_nick(NormalizedNick("dizzikss"))

    assert profile is not None
    assert profile.referrals_count == 1
    assert profile.referral_role == "🧭 Скаут"


async def test_nick_display_falls_back_to_normalized_nick_when_unmapped(
    connection: aiosqlite.Connection, synced_at: str
) -> None:
    repo = UsersCacheRepository(connection)
    await repo.replace_all([_profile()], nick_displays={}, synced_at=synced_at)

    assert await repo.get_nick_display(NormalizedNick("dizzikss")) == "dizzikss"


async def test_nick_display_uses_provided_original_casing(
    connection: aiosqlite.Connection, synced_at: str
) -> None:
    repo = UsersCacheRepository(connection)
    await repo.replace_all(
        [_profile()], nick_displays={NormalizedNick("dizzikss"): "DizzikSS"}, synced_at=synced_at
    )

    assert await repo.get_nick_display(NormalizedNick("dizzikss")) == "DizzikSS"


async def test_get_by_nicks_returns_only_the_nicks_found(
    connection: aiosqlite.Connection, synced_at: str
) -> None:
    repo = UsersCacheRepository(connection)
    await repo.replace_all(
        [_profile("alice"), _profile("bob")], nick_displays={}, synced_at=synced_at
    )

    found = await repo.get_by_nicks([NormalizedNick("alice"), NormalizedNick("ghost")])

    assert set(found) == {NormalizedNick("alice")}
    assert found[NormalizedNick("alice")].nick == "alice"


async def test_get_by_nicks_empty_input_returns_empty_dict(
    connection: aiosqlite.Connection, synced_at: str
) -> None:
    repo = UsersCacheRepository(connection)
    await repo.replace_all([_profile()], nick_displays={}, synced_at=synced_at)

    assert await repo.get_by_nicks([]) == {}


async def test_get_nick_displays_returns_only_the_nicks_found(
    connection: aiosqlite.Connection, synced_at: str
) -> None:
    repo = UsersCacheRepository(connection)
    await repo.replace_all(
        [_profile("alice"), _profile("bob")],
        nick_displays={NormalizedNick("alice"): "Alice", NormalizedNick("bob"): "Bob"},
        synced_at=synced_at,
    )

    found = await repo.get_nick_displays([NormalizedNick("alice"), NormalizedNick("ghost")])

    assert found == {NormalizedNick("alice"): "Alice"}


async def test_get_by_discord_id(connection: aiosqlite.Connection, synced_at: str) -> None:
    repo = UsersCacheRepository(connection)
    await repo.replace_all([_profile(discord_id=123456)], nick_displays={}, synced_at=synced_at)

    profile = await repo.get_by_discord_id(123456)

    assert profile is not None
    assert profile.nick == "dizzikss"


async def test_get_by_discord_id_returns_none_when_unbound(
    connection: aiosqlite.Connection, synced_at: str
) -> None:
    repo = UsersCacheRepository(connection)
    await repo.replace_all([_profile(discord_id=None)], nick_displays={}, synced_at=synced_at)

    assert await repo.get_by_discord_id(999) is None


async def test_replace_all_clears_previous_users(
    connection: aiosqlite.Connection, synced_at: str
) -> None:
    repo = UsersCacheRepository(connection)
    await repo.replace_all([_profile("first")], nick_displays={}, synced_at=synced_at)
    await repo.replace_all([_profile("second")], nick_displays={}, synced_at=synced_at)

    assert await repo.get_by_nick(NormalizedNick("first")) is None
    assert await repo.get_by_nick(NormalizedNick("second")) is not None


async def test_upsert_many_updates_existing_row(
    connection: aiosqlite.Connection, synced_at: str
) -> None:
    repo = UsersCacheRepository(connection)
    await repo.replace_all([_profile(coins=1)], nick_displays={}, synced_at=synced_at)

    await repo.upsert_many([_profile(coins=99)], nick_displays={}, synced_at=synced_at)

    profile = await repo.get_by_nick(NormalizedNick("dizzikss"))
    assert profile is not None
    assert profile.coins == 99


async def test_all_orders_by_sheet_row(connection: aiosqlite.Connection, synced_at: str) -> None:
    repo = UsersCacheRepository(connection)
    await repo.replace_all(
        [_profile("b", row=5), _profile("a", row=3)], nick_displays={}, synced_at=synced_at
    )

    profiles = await repo.all()

    assert [p.nick for p in profiles] == ["a", "b"]


async def test_last_synced_at_is_none_when_empty(connection: aiosqlite.Connection) -> None:
    repo = UsersCacheRepository(connection)
    assert await repo.last_synced_at() is None


async def test_last_synced_at_reflects_the_stamp(
    connection: aiosqlite.Connection, synced_at: str
) -> None:
    repo = UsersCacheRepository(connection)
    await repo.replace_all([_profile()], nick_displays={}, synced_at=synced_at)

    result = await repo.last_synced_at()

    assert result == datetime.fromisoformat(synced_at)
