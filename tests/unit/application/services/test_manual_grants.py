"""Tests for `stalbot.application.services.manual_grants.ManualGrantService` (PLAN.md §10.12).

`SheetsClient`/`RoleGateway` are mocked; the cache repositories are real,
SQLite-backed, for genuine round-trip confidence (same approach as
`test_transaction_service.py`).
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest
import pytest_asyncio

from stalbot.application.dto.progression_state import ProgressionState
from stalbot.application.ports.role_gateway import RoleDiff, RoleGateway, RoleSet
from stalbot.application.services.manual_grants import ManualGrantService
from stalbot.domain.entities.transaction import TransactionRecord
from stalbot.domain.enums import DealType
from stalbot.domain.errors import NoTransactionsYetError
from stalbot.domain.nick import NormalizedNick
from stalbot.domain.progression.ranks import RankLadder
from stalbot.infrastructure.cache.db import CacheDb
from stalbot.infrastructure.cache.repositories.progression_state import ProgressionStateRepository
from stalbot.infrastructure.cache.repositories.transactions import TransactionsCacheRepository
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
    client.write_verified = AsyncMock()
    client.batch_update = AsyncMock()
    return client


def _fake_roles() -> MagicMock:
    gateway = MagicMock(spec=RoleGateway)
    gateway.sync_roles = AsyncMock(return_value=RoleDiff(granted=(), revoked=()))
    return gateway


def _service(
    connection: aiosqlite.Connection, *, sheets: MagicMock, roles: MagicMock
) -> tuple[ManualGrantService, TransactionsCacheRepository, ProgressionStateRepository]:
    transactions = TransactionsCacheRepository(connection)
    progression_state = ProgressionStateRepository(connection)
    service = ManualGrantService(
        sheets,
        transactions,
        UsersCacheRepository(connection),
        progression_state,
        roles,
        clock=_FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC)),
    )
    return service, transactions, progression_state


def _record(**overrides: object) -> TransactionRecord:
    defaults: dict[str, object] = {
        "row": 3,
        "at": datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
        "nick": NormalizedNick("scaryyyyy"),
        "nick_display": "Scaryyyyy",
        "deal_type": DealType.PURCHASE,
        "amount": Decimal(299900),
        "coins": 1,
        "xp": 10,
        "referrer": None,
    }
    defaults.update(overrides)
    return TransactionRecord(**defaults)  # type: ignore[arg-type]


async def test_current_referrer_is_none_with_no_transactions(
    connection: aiosqlite.Connection,
) -> None:
    service, _transactions, _state = _service(
        connection, sheets=_fake_sheets(), roles=_fake_roles()
    )

    assert await service.current_referrer("Scaryyyyy") is None


async def test_current_referrer_returns_the_first_rows_referrer(
    connection: aiosqlite.Connection,
) -> None:
    service, transactions, _state = _service(connection, sheets=_fake_sheets(), roles=_fake_roles())
    await transactions.upsert_many([_record(row=3, referrer=NormalizedNick("othernick"))])

    assert await service.current_referrer("Scaryyyyy") == NormalizedNick("othernick")


async def test_set_referral_raises_without_any_transactions(
    connection: aiosqlite.Connection,
) -> None:
    service, _transactions, _state = _service(
        connection, sheets=_fake_sheets(), roles=_fake_roles()
    )

    with pytest.raises(NoTransactionsYetError):
        await service.set_referral("Scaryyyyy", "OtherNick", 1, 2)


async def test_set_referral_writes_h_on_the_first_row(connection: aiosqlite.Connection) -> None:
    sheets = _fake_sheets()
    service, transactions, _state = _service(connection, sheets=sheets, roles=_fake_roles())
    await transactions.upsert_many([_record(row=3), _record(row=5)])

    result = await service.set_referral("Scaryyyyy", "OtherNick", 1, 2)

    sheets.write_verified.assert_awaited_once_with({"DataBase!H3": [["OtherNick"]]})
    assert result.row == 3
    updated = await transactions.get_by_row(3)
    assert updated is not None
    assert updated.referrer == NormalizedNick("othernick")


async def test_set_referral_reports_the_previous_referrer(
    connection: aiosqlite.Connection,
) -> None:
    service, transactions, _state = _service(connection, sheets=_fake_sheets(), roles=_fake_roles())
    await transactions.upsert_many([_record(row=3, referrer=NormalizedNick("oldreferrer"))])

    result = await service.set_referral("Scaryyyyy", "NewReferrer", 1, 2)

    assert result.previous_referrer == NormalizedNick("oldreferrer")


async def test_set_referral_binds_both_discord_ids(connection: aiosqlite.Connection) -> None:
    from stalbot.domain.entities.user_profile import UserProfile

    users = UsersCacheRepository(connection)
    await users.replace_all(
        [
            UserProfile(
                row=3,
                nick=NormalizedNick("scaryyyyy"),
                discord_id=None,
                coins=0,
                xp=0,
                buy_turnover=Decimal(0),
                sell_turnover=Decimal(0),
                total_turnover=Decimal(0),
                referrals_count=0,
                is_booster=False,
                rank=None,
                referral_role=None,
            ),
            UserProfile(
                row=7,
                nick=NormalizedNick("othernick"),
                discord_id=None,
                coins=0,
                xp=0,
                buy_turnover=Decimal(0),
                sell_turnover=Decimal(0),
                total_turnover=Decimal(0),
                referrals_count=0,
                is_booster=False,
                rank=None,
                referral_role=None,
            ),
        ],
        nick_displays={},
        synced_at="2026-08-02T11:00:00+03:00",
    )
    sheets = _fake_sheets()
    transactions = TransactionsCacheRepository(connection)
    await transactions.upsert_many([_record(row=3)])
    service = ManualGrantService(
        sheets,
        transactions,
        users,
        ProgressionStateRepository(connection),
        _fake_roles(),
        clock=_FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC)),
    )

    result = await service.set_referral("Scaryyyyy", "OtherNick", 111, 222)

    assert result.player_discord_bound is True
    assert result.referrer_discord_bound is True
    assert (await users.get_by_nick(NormalizedNick("scaryyyyy"))).discord_id == 111  # type: ignore[union-attr]
    assert (await users.get_by_nick(NormalizedNick("othernick"))).discord_id == 222  # type: ignore[union-attr]


async def test_set_rank_grants_the_role_and_sets_manual_flag(
    connection: aiosqlite.Connection,
) -> None:
    roles = _fake_roles()
    service, _transactions, state = _service(connection, sheets=_fake_sheets(), roles=roles)
    tier = RankLadder().by_key("elite")
    assert tier is not None

    result = await service.set_rank("Scaryyyyy", 999, tier, revoke=False)

    assert result.granted is True
    roles.sync_roles.assert_awaited_once_with(
        999, RoleSet(desired=frozenset({tier.role_id}), universe=RankLadder().role_ids)
    )
    stored = await state.get(NormalizedNick("scaryyyyy"))
    assert stored is not None
    assert stored.manual_rank_role is True


async def test_set_rank_toggle_off_revokes_and_clears_the_flag(
    connection: aiosqlite.Connection,
) -> None:
    roles = _fake_roles()
    service, _transactions, state = _service(connection, sheets=_fake_sheets(), roles=roles)
    tier = RankLadder().by_key("elite")
    assert tier is not None
    await state.upsert(
        ProgressionState(
            nick=NormalizedNick("scaryyyyy"),
            last_rank="🔷 Premium",
            last_referral_role="🧭 Скаут",
            manual_rank_role=True,
            announced_at=None,
        )
    )

    result = await service.set_rank("Scaryyyyy", 999, tier, revoke=True)

    assert result.granted is False
    roles.sync_roles.assert_awaited_once_with(
        999, RoleSet(desired=frozenset(), universe=RankLadder().role_ids)
    )
    stored = await state.get(NormalizedNick("scaryyyyy"))
    assert stored is not None
    assert stored.manual_rank_role is False
    # Sheet-derived tracking (last_rank/last_referral_role) survives the toggle.
    assert stored.last_rank == "🔷 Premium"
    assert stored.last_referral_role == "🧭 Скаут"
