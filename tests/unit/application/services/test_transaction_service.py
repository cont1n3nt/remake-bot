"""Tests for `stalbot.application.services.transactions.TransactionService` (PLAN.md §7.4, §10.1).

`SheetsClient`/`CacheSync` are mocked (their own behavior is covered by
`test_client.py`/`test_sync.py`) — this file is about whether
`TransactionService` orchestrates them correctly. The cache repositories
are real, SQLite-backed, for genuine round-trip confidence.
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest
import pytest_asyncio

from stalbot.application.dto.transaction_request import AddTransactionRequest
from stalbot.application.services.transactions import TransactionService
from stalbot.domain.entities.user_profile import UserProfile
from stalbot.domain.enums import DealType
from stalbot.domain.errors import SheetsWriteConflictError
from stalbot.domain.nick import NormalizedNick
from stalbot.infrastructure.cache.db import CacheDb
from stalbot.infrastructure.cache.repositories.idempotency import IdempotencyRepository
from stalbot.infrastructure.cache.repositories.transactions import TransactionsCacheRepository
from stalbot.infrastructure.cache.repositories.users import UsersCacheRepository
from stalbot.infrastructure.cache.sync import CacheSync
from stalbot.infrastructure.sheets.client import SheetsClient


@pytest_asyncio.fixture
async def connection(tmp_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    db = CacheDb(tmp_path / "cache.sqlite3")
    conn = await db.connect()
    yield conn
    await db.close()


class _FixedClock:
    def __init__(self, now: datetime) -> None:
        self.current = now

    def now(self) -> datetime:
        return self.current


def _fake_sheets() -> MagicMock:
    client = MagicMock(spec=SheetsClient)
    client.batch_get = AsyncMock(return_value={})  # candidate row reads as empty by default
    client.write_verified = AsyncMock()
    client.read_until = AsyncMock(return_value={"DataBase!F3": [[1]], "DataBase!G3": [[10]]})
    client.read_formula_extent = AsyncMock(return_value=1000)
    client.copy_formula_down = AsyncMock()
    client.batch_update = AsyncMock()
    return client


def _fake_cache_sync() -> MagicMock:
    cache_sync = MagicMock(spec=CacheSync)
    cache_sync.sync_users_and_transactions = AsyncMock()
    return cache_sync


def _service(
    connection: aiosqlite.Connection,
    *,
    sheets: MagicMock,
    cache_sync: MagicMock,
    clock: _FixedClock,
) -> TransactionService:
    return TransactionService(
        sheets,
        TransactionsCacheRepository(connection),
        UsersCacheRepository(connection),
        IdempotencyRepository(connection),
        cache_sync,
        clock=clock,
    )


def _request(**overrides: object) -> AddTransactionRequest:
    defaults: dict[str, object] = {
        "nick": "Scaryyyyy",
        "deal_type": DealType.PURCHASE,
        "amount": Decimal(299900),
        "discord_id": 12345,
        "idempotency_key": "interaction-1",
        "referrer_nick": None,
        "force_rebind": False,
    }
    defaults.update(overrides)
    return AddTransactionRequest(**defaults)  # type: ignore[arg-type]


async def test_register_writes_the_row_and_returns_formula_values(
    connection: aiosqlite.Connection,
) -> None:
    sheets = _fake_sheets()
    cache_sync = _fake_cache_sync()
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service = _service(connection, sheets=sheets, cache_sync=cache_sync, clock=clock)

    result = await service.register(_request())

    sheets.write_verified.assert_awaited_once()
    (data,), _ = sheets.write_verified.call_args
    assert "DataBase!A3:E3" in data
    row = data["DataBase!A3:E3"][0]
    assert row[1] == "Scaryyyyy"
    assert row[2] is True  # purchase flag
    assert row[3] is False  # sale flag
    assert row[4] == 299900

    assert result.record.row == 3
    assert result.record.coins == 1
    assert result.record.xp == 10
    assert result.formula_pending is False
    cache_sync.sync_users_and_transactions.assert_awaited_once()


async def test_register_rounds_fractional_amount_consistently_for_sheet_and_cache(
    connection: aiosqlite.Connection,
) -> None:
    """APP-2: the sheet write and the cached/returned record must agree on the same value.

    A bare `int(...)` truncates toward zero (150.5 -> 150), while the cached
    `Decimal` kept the untruncated value — the two used to disagree.
    """
    sheets = _fake_sheets()
    cache_sync = _fake_cache_sync()
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service = _service(connection, sheets=sheets, cache_sync=cache_sync, clock=clock)

    result = await service.register(_request(amount=Decimal("150.5")))

    (data,), _ = sheets.write_verified.call_args
    written_amount = data["DataBase!A3:E3"][0][4]
    assert written_amount == 151  # ROUND_HALF_UP, not truncation
    assert result.record.amount == Decimal(151)


async def test_register_is_idempotent_for_the_same_key(connection: aiosqlite.Connection) -> None:
    sheets = _fake_sheets()
    cache_sync = _fake_cache_sync()
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service = _service(connection, sheets=sheets, cache_sync=cache_sync, clock=clock)
    request = _request()

    first = await service.register(request)
    sheets.write_verified.reset_mock()
    second = await service.register(request)

    sheets.write_verified.assert_not_called()
    # nick_display on replay comes from `users` (LEFT JOIN), which the mocked
    # cache_sync never actually populates here — compare the fields that
    # matter instead of full equality.
    assert second.record.row == first.record.row
    assert second.record.amount == first.record.amount
    assert second.record.coins == first.record.coins
    assert second.record.xp == first.record.xp
    assert second.discord_bound is False
    assert second.formula_pending is False


async def test_register_serializes_concurrent_calls_with_the_same_idempotency_key(
    connection: aiosqlite.Connection,
) -> None:
    """CLUSTER-1: two concurrent registrations for the same deal must not both write.

    Without a lock spanning the whole find-row -> write -> verify -> record
    path, both calls can pass the idempotency replay check before either has
    recorded it, compute the same "free" row, and both write — the second
    write clobbering the first (or worse, both landing on different rows).
    """
    sheets = _fake_sheets()

    async def _slow_write(data: object) -> None:
        await asyncio.sleep(0)  # yield control so the two register() calls can interleave

    sheets.write_verified.side_effect = _slow_write
    cache_sync = _fake_cache_sync()
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service = _service(connection, sheets=sheets, cache_sync=cache_sync, clock=clock)
    request = _request()

    first, second = await asyncio.gather(service.register(request), service.register(request))

    assert sheets.write_verified.await_count == 1  # only the winner actually wrote
    assert first.record.row == second.record.row
    assert first.record.amount == second.record.amount


async def test_register_writes_referrer_only_on_the_first_transaction(
    connection: aiosqlite.Connection,
) -> None:
    sheets = _fake_sheets()
    cache_sync = _fake_cache_sync()
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service = _service(connection, sheets=sheets, cache_sync=cache_sync, clock=clock)

    await service.register(_request(idempotency_key="i1", referrer_nick="OtherNick"))
    (first_data,), _ = sheets.write_verified.call_args
    assert "DataBase!H3" in first_data

    sheets.write_verified.reset_mock()
    await service.register(_request(idempotency_key="i2", referrer_nick="OtherNick"))
    (second_data,), _ = sheets.write_verified.call_args
    assert not any(ref.startswith("DataBase!H") for ref in second_data)


async def test_register_finds_the_next_free_row_when_the_first_is_occupied(
    connection: aiosqlite.Connection,
) -> None:
    sheets = _fake_sheets()
    sheets.batch_get = AsyncMock(
        side_effect=[
            {"DataBase!B3": [["SomeoneElse"]]},  # row 3 occupied
            {},  # row 4 free
        ]
    )
    cache_sync = _fake_cache_sync()
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service = _service(connection, sheets=sheets, cache_sync=cache_sync, clock=clock)

    result = await service.register(_request())

    assert result.record.row == 4


async def test_register_raises_when_no_free_row_is_ever_found(
    connection: aiosqlite.Connection,
) -> None:
    sheets = _fake_sheets()
    sheets.batch_get = AsyncMock(side_effect=lambda refs: {refs[0]: [["Occupied"]]})
    cache_sync = _fake_cache_sync()
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service = _service(connection, sheets=sheets, cache_sync=cache_sync, clock=clock)

    with pytest.raises(SheetsWriteConflictError):
        await service.register(_request())


async def test_register_falls_back_to_copy_formula_down_when_formulas_are_pending(
    connection: aiosqlite.Connection,
) -> None:
    sheets = _fake_sheets()
    sheets.read_until = AsyncMock(
        side_effect=[
            {},  # first wait: nothing yet
            {"DataBase!F3": [[2]], "DataBase!G3": [[20]]},  # after copyPaste fallback
        ]
    )
    cache_sync = _fake_cache_sync()
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service = _service(connection, sheets=sheets, cache_sync=cache_sync, clock=clock)

    result = await service.register(_request())

    sheets.copy_formula_down.assert_awaited_once()
    assert result.record.coins == 2
    assert result.record.xp == 20
    assert result.formula_pending is False


async def test_register_reports_formula_pending_when_the_fallback_also_fails(
    connection: aiosqlite.Connection,
) -> None:
    sheets = _fake_sheets()
    sheets.read_until = AsyncMock(return_value={})
    cache_sync = _fake_cache_sync()
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service = _service(connection, sheets=sheets, cache_sync=cache_sync, clock=clock)

    result = await service.register(_request())

    assert result.formula_pending is True
    assert result.record.coins == 0
    assert result.record.xp == 0


async def test_register_binds_an_unbound_discord_id(connection: aiosqlite.Connection) -> None:
    users = UsersCacheRepository(connection)
    await users.replace_all(
        [_profile(discord_id=None)],
        nick_displays={NormalizedNick("scaryyyyy"): "Scaryyyyy"},
        synced_at="2026-08-02T11:00:00+03:00",
    )
    sheets = _fake_sheets()
    cache_sync = _fake_cache_sync()
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service = _service(connection, sheets=sheets, cache_sync=cache_sync, clock=clock)

    result = await service.register(_request(discord_id=999))

    assert result.discord_bound is True
    sheets.batch_update.assert_awaited_once_with({"DataBase!I3": [[999]]})
    profile = await users.get_by_nick(NormalizedNick("scaryyyyy"))
    assert profile is not None
    assert profile.discord_id == 999


async def test_register_does_not_rebind_without_force(connection: aiosqlite.Connection) -> None:
    users = UsersCacheRepository(connection)
    await users.replace_all(
        [_profile(discord_id=111)], nick_displays={}, synced_at="2026-08-02T11:00:00+03:00"
    )
    sheets = _fake_sheets()
    cache_sync = _fake_cache_sync()
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service = _service(connection, sheets=sheets, cache_sync=cache_sync, clock=clock)

    result = await service.register(_request(discord_id=999, force_rebind=False))

    assert result.discord_bound is False
    sheets.batch_update.assert_not_called()


async def test_register_rebinds_when_forced(connection: aiosqlite.Connection) -> None:
    users = UsersCacheRepository(connection)
    await users.replace_all(
        [_profile(discord_id=111)], nick_displays={}, synced_at="2026-08-02T11:00:00+03:00"
    )
    sheets = _fake_sheets()
    cache_sync = _fake_cache_sync()
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service = _service(connection, sheets=sheets, cache_sync=cache_sync, clock=clock)

    result = await service.register(_request(discord_id=999, force_rebind=True))

    assert result.discord_bound is True
    sheets.batch_update.assert_awaited_once_with({"DataBase!I3": [[999]]})


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
