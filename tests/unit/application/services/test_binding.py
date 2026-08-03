"""Tests for `stalbot.application.services.binding.bind_discord` (PLAN.md §6.1).

Shared by `TransactionService` (M4) and `ManualGrantService` (M8) — covered
once here rather than duplicated in both services' test files.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest_asyncio

from stalbot.application.services.binding import bind_discord
from stalbot.domain.entities.user_profile import UserProfile
from stalbot.domain.nick import NormalizedNick
from stalbot.infrastructure.cache.db import CacheDb
from stalbot.infrastructure.cache.repositories.users import UsersCacheRepository
from stalbot.infrastructure.sheets.client import SheetsClient


@pytest_asyncio.fixture
async def connection(tmp_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    db = CacheDb(tmp_path / "cache.sqlite3")
    conn = await db.connect()
    yield conn
    await db.close()


class _FixedClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


def _fake_sheets() -> MagicMock:
    client = MagicMock(spec=SheetsClient)
    client.batch_update = AsyncMock()
    return client


def _profile(**overrides: object) -> UserProfile:
    defaults: dict[str, object] = {
        "row": 3,
        "nick": NormalizedNick("scaryyyyy"),
        "discord_id": None,
        "coins": 0,
        "xp": 0,
        "buy_turnover": Decimal(0),
        "sell_turnover": Decimal(0),
        "total_turnover": Decimal(0),
        "referrals_count": 0,
        "is_booster": False,
        "rank": None,
        "referral_role": None,
    }
    defaults.update(overrides)
    return UserProfile(**defaults)  # type: ignore[arg-type]


async def test_binds_an_unbound_nick(connection: aiosqlite.Connection) -> None:
    users = UsersCacheRepository(connection)
    await users.replace_all(
        [_profile(discord_id=None)], nick_displays={}, synced_at="2026-08-02T11:00:00+03:00"
    )
    sheets = _fake_sheets()
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))

    bound = await bind_discord(users, sheets, clock, NormalizedNick("scaryyyyy"), 999, force=False)

    assert bound is True
    sheets.batch_update.assert_awaited_once_with({"DataBase!I3": [[999]]})
    profile = await users.get_by_nick(NormalizedNick("scaryyyyy"))
    assert profile is not None
    assert profile.discord_id == 999


async def test_no_op_for_unknown_nick(connection: aiosqlite.Connection) -> None:
    users = UsersCacheRepository(connection)
    sheets = _fake_sheets()
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))

    bound = await bind_discord(users, sheets, clock, NormalizedNick("nobody"), 999, force=False)

    assert bound is False
    sheets.batch_update.assert_not_called()


async def test_no_op_when_already_bound_to_same_id(connection: aiosqlite.Connection) -> None:
    users = UsersCacheRepository(connection)
    await users.replace_all(
        [_profile(discord_id=999)], nick_displays={}, synced_at="2026-08-02T11:00:00+03:00"
    )
    sheets = _fake_sheets()
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))

    bound = await bind_discord(users, sheets, clock, NormalizedNick("scaryyyyy"), 999, force=False)

    assert bound is False
    sheets.batch_update.assert_not_called()


async def test_does_not_rebind_without_force(connection: aiosqlite.Connection) -> None:
    users = UsersCacheRepository(connection)
    await users.replace_all(
        [_profile(discord_id=111)], nick_displays={}, synced_at="2026-08-02T11:00:00+03:00"
    )
    sheets = _fake_sheets()
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))

    bound = await bind_discord(users, sheets, clock, NormalizedNick("scaryyyyy"), 999, force=False)

    assert bound is False
    sheets.batch_update.assert_not_called()


async def test_rebinds_when_forced(connection: aiosqlite.Connection) -> None:
    users = UsersCacheRepository(connection)
    await users.replace_all(
        [_profile(discord_id=111)], nick_displays={}, synced_at="2026-08-02T11:00:00+03:00"
    )
    sheets = _fake_sheets()
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))

    bound = await bind_discord(users, sheets, clock, NormalizedNick("scaryyyyy"), 999, force=True)

    assert bound is True
    sheets.batch_update.assert_awaited_once_with({"DataBase!I3": [[999]]})
