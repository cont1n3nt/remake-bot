"""Tests for `stalbot.application.services.progression.ProgressionService` (PLAN.md §9.2).

`UsersCacheRepository`/`ProgressionStateRepository` are real, SQLite-backed
(genuine round-trip confidence); `RoleGateway`/`AuditGateway` are fakes
(they are `Protocol` ports, built exactly for this); `AuditService` is a
`MagicMock` since it is a concrete class whose own behavior is already
covered by `test_audit.py`.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest_asyncio

from stalbot.application.dto.progression_state import ProgressionState
from stalbot.application.ports.role_gateway import RoleDiff, RoleSet
from stalbot.application.services.audit import AuditService
from stalbot.application.services.progression import ProgressionService
from stalbot.domain.entities.user_profile import UserProfile
from stalbot.domain.nick import NormalizedNick
from stalbot.domain.progression.ranks import RankLadder
from stalbot.infrastructure.cache.db import CacheDb
from stalbot.infrastructure.cache.repositories.progression_state import ProgressionStateRepository
from stalbot.infrastructure.cache.repositories.users import UsersCacheRepository
from stalbot.infrastructure.sheets.client import SheetsClient
from stalbot.presentation.embeds.factory import EmbedFactory


@pytest_asyncio.fixture
async def connection(tmp_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    db = CacheDb(tmp_path / "cache.sqlite3")
    conn = await db.connect()
    yield conn
    await db.close()


class _FakeRoleGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[int, RoleSet]] = []

    async def sync_roles(self, member_id: int, target: RoleSet) -> RoleDiff:
        self.calls.append((member_id, target))
        return RoleDiff(granted=tuple(target.desired), revoked=())


class _FakeAuditGateway:
    def __init__(self) -> None:
        self.batches: list[list[object]] = []

    async def send_batch(self, embeds: list[object]) -> None:
        self.batches.append(list(embeds))


class _FakeChannel:
    def __init__(self, name: str = "general") -> None:
        self.name = name
        self.sent: list[object] = []

    async def send(self, *, embed: object) -> None:
        self.sent.append(embed)


class _FixedClock:
    def __init__(self, now: datetime) -> None:
        self.current = now

    def now(self) -> datetime:
        return self.current


def _profile(nick: str = "scaryyyyy", **overrides: object) -> UserProfile:
    defaults: dict[str, object] = {
        "row": 3,
        "nick": NormalizedNick(nick),
        "discord_id": 12345,
        "coins": 1240,
        "xp": 3780,
        "buy_turnover": Decimal(0),
        "sell_turnover": Decimal(0),
        "total_turnover": Decimal(0),
        "referrals_count": 0,
        "is_booster": False,
        "rank": "💎 Elite",
        "referral_role": None,
    }
    defaults.update(overrides)
    return UserProfile(**defaults)  # type: ignore[arg-type]


def _service(
    connection: aiosqlite.Connection,
    *,
    roles: _FakeRoleGateway,
    audit_gateway: _FakeAuditGateway,
    audit_service: MagicMock,
    clock: _FixedClock,
    sheets: MagicMock | None = None,
) -> ProgressionService:
    return ProgressionService(
        UsersCacheRepository(connection),
        ProgressionStateRepository(connection),
        roles,
        audit_gateway,  # type: ignore[arg-type]
        audit_service,
        EmbedFactory(),
        sheets=sheets or _fake_sheets_client(),
        clock=clock,
    )


def _fake_sheets_client() -> MagicMock:
    client = MagicMock(spec=SheetsClient)
    client.batch_update = AsyncMock()
    return client


async def test_player_without_discord_id_is_skipped(connection: aiosqlite.Connection) -> None:
    users = UsersCacheRepository(connection)
    await users.replace_all(
        [_profile(discord_id=None)], nick_displays={}, synced_at="2026-08-02T12:00:00+03:00"
    )
    roles = _FakeRoleGateway()
    service = _service(
        connection,
        roles=roles,
        audit_gateway=_FakeAuditGateway(),
        audit_service=MagicMock(spec=AuditService),
        clock=_FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC)),
    )

    promotions = await service.sync([NormalizedNick("scaryyyyy")])

    assert promotions == []
    assert roles.calls == []


async def test_first_ever_sync_records_baseline_without_announcing(
    connection: aiosqlite.Connection,
) -> None:
    users = UsersCacheRepository(connection)
    await users.replace_all([_profile()], nick_displays={}, synced_at="2026-08-02T12:00:00+03:00")
    roles = _FakeRoleGateway()
    audit_gateway = _FakeAuditGateway()
    audit_service = MagicMock(spec=AuditService)
    service = _service(
        connection,
        roles=roles,
        audit_gateway=audit_gateway,
        audit_service=audit_service,
        clock=_FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC)),
    )

    promotions = await service.sync([NormalizedNick("scaryyyyy")])

    assert promotions == []
    assert len(roles.calls) == 1  # roles are still reconciled on first sync
    assert audit_gateway.batches == []
    audit_service.record.assert_not_called()

    state = await ProgressionStateRepository(connection).get(NormalizedNick("scaryyyyy"))
    assert state is not None
    assert state.last_rank == "💎 Elite"


async def test_genuine_rank_promotion_is_announced_and_role_switched(
    connection: aiosqlite.Connection,
) -> None:
    users = UsersCacheRepository(connection)
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))

    # Baseline: player was already tracked at Prestige.
    await users.replace_all(
        [_profile(rank="💠 Prestige")], nick_displays={}, synced_at="2026-08-02T11:00:00+03:00"
    )
    roles = _FakeRoleGateway()
    audit_gateway = _FakeAuditGateway()
    audit_service = MagicMock(spec=AuditService)
    service = _service(
        connection,
        roles=roles,
        audit_gateway=audit_gateway,
        audit_service=audit_service,
        clock=clock,
    )
    await service.sync([NormalizedNick("scaryyyyy")])  # establishes baseline, no promotion
    roles.calls.clear()

    # Now the sheet says Elite.
    await users.replace_all(
        [_profile(rank="💎 Elite")], nick_displays={}, synced_at="2026-08-02T12:00:00+03:00"
    )
    channel = _FakeChannel()

    promotions = await service.sync([NormalizedNick("scaryyyyy")], announce_to=channel)  # type: ignore[arg-type]

    assert len(promotions) == 1
    promotion = promotions[0]
    assert promotion.axis == "rank"
    assert promotion.label == "💎 Elite"
    assert promotion.coins == 1240
    assert promotion.xp == 3780

    assert len(channel.sent) == 1
    assert audit_gateway.batches == []  # went to the explicit channel, not the log-channel fallback
    audit_service.record.assert_called_once()

    (member_id, role_set) = roles.calls[0]
    assert member_id == 12345
    assert role_set.desired  # non-empty: the new Elite role id is desired


async def test_downgrade_or_lateral_change_is_not_announced(
    connection: aiosqlite.Connection,
) -> None:
    users = UsersCacheRepository(connection)
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    roles = _FakeRoleGateway()
    audit_gateway = _FakeAuditGateway()
    audit_service = MagicMock(spec=AuditService)
    service = _service(
        connection,
        roles=roles,
        audit_gateway=audit_gateway,
        audit_service=audit_service,
        clock=clock,
    )

    await users.replace_all(
        [_profile(rank="💎 Elite")], nick_displays={}, synced_at="2026-08-02T11:00:00+03:00"
    )
    await service.sync([NormalizedNick("scaryyyyy")])

    await users.replace_all(
        [_profile(rank="💠 Prestige")], nick_displays={}, synced_at="2026-08-02T12:00:00+03:00"
    )
    promotions = await service.sync([NormalizedNick("scaryyyyy")])

    assert promotions == []
    audit_service.record.assert_not_called()


async def test_no_change_still_reconciles_roles_but_does_not_announce(
    connection: aiosqlite.Connection,
) -> None:
    users = UsersCacheRepository(connection)
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    roles = _FakeRoleGateway()
    audit_service = MagicMock(spec=AuditService)
    service = _service(
        connection,
        roles=roles,
        audit_gateway=_FakeAuditGateway(),
        audit_service=audit_service,
        clock=clock,
    )

    await users.replace_all([_profile()], nick_displays={}, synced_at="2026-08-02T11:00:00+03:00")
    await service.sync([NormalizedNick("scaryyyyy")])
    promotions = await service.sync([NormalizedNick("scaryyyyy")])

    assert promotions == []
    assert len(roles.calls) == 2
    audit_service.record.assert_not_called()


async def test_announce_to_none_falls_back_to_audit_gateway(
    connection: aiosqlite.Connection,
) -> None:
    users = UsersCacheRepository(connection)
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    audit_gateway = _FakeAuditGateway()
    service = _service(
        connection,
        roles=_FakeRoleGateway(),
        audit_gateway=audit_gateway,
        audit_service=MagicMock(spec=AuditService),
        clock=clock,
    )

    await users.replace_all(
        [_profile(rank="💠 Prestige")], nick_displays={}, synced_at="2026-08-02T11:00:00+03:00"
    )
    await service.sync([NormalizedNick("scaryyyyy")])
    await users.replace_all(
        [_profile(rank="💎 Elite")], nick_displays={}, synced_at="2026-08-02T12:00:00+03:00"
    )

    promotions = await service.sync([NormalizedNick("scaryyyyy")])  # announce_to defaults to None

    assert len(promotions) == 1
    assert len(audit_gateway.batches) == 1


async def test_sync_with_no_nicks_covers_the_whole_cached_base(
    connection: aiosqlite.Connection,
) -> None:
    users = UsersCacheRepository(connection)
    await users.replace_all(
        [_profile("first"), _profile("second", discord_id=54321)],
        nick_displays={},
        synced_at="2026-08-02T12:00:00+03:00",
    )
    roles = _FakeRoleGateway()
    service = _service(
        connection,
        roles=roles,
        audit_gateway=_FakeAuditGateway(),
        audit_service=MagicMock(spec=AuditService),
        clock=_FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC)),
    )

    await service.sync()

    assert len(roles.calls) == 2


async def test_unrecognized_rank_label_is_not_included_in_desired_roles(
    connection: aiosqlite.Connection,
) -> None:
    users = UsersCacheRepository(connection)
    await users.replace_all(
        [_profile(rank="not a real rank")],
        nick_displays={},
        synced_at="2026-08-02T12:00:00+03:00",
    )
    roles = _FakeRoleGateway()
    service = _service(
        connection,
        roles=roles,
        audit_gateway=_FakeAuditGateway(),
        audit_service=MagicMock(spec=AuditService),
        clock=_FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC)),
    )

    await service.sync([NormalizedNick("scaryyyyy")])

    assert roles.calls[0][1].desired == frozenset()


async def test_sync_booster_flag_writes_column_q_and_updates_cache(
    connection: aiosqlite.Connection,
) -> None:
    users = UsersCacheRepository(connection)
    await users.replace_all(
        [_profile(is_booster=False)],
        nick_displays={NormalizedNick("scaryyyyy"): "Scaryyyyy"},
        synced_at="2026-08-02T11:00:00+03:00",
    )
    sheets = _fake_sheets_client()
    roles = _FakeRoleGateway()
    service = _service(
        connection,
        roles=roles,
        audit_gateway=_FakeAuditGateway(),
        audit_service=MagicMock(spec=AuditService),
        clock=_FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC)),
        sheets=sheets,
    )

    await service.sync_booster_flag(12345, True)

    sheets.batch_update.assert_awaited_once_with({"DataBase!Q3": [[True]]})
    updated = await users.get_by_nick(NormalizedNick("scaryyyyy"))
    assert updated is not None
    assert updated.is_booster is True
    assert await users.get_nick_display(NormalizedNick("scaryyyyy")) == "Scaryyyyy"
    assert len(roles.calls) == 1  # sync_booster_flag also resyncs progression


async def test_sync_booster_flag_is_a_no_op_when_already_correct(
    connection: aiosqlite.Connection,
) -> None:
    users = UsersCacheRepository(connection)
    await users.replace_all(
        [_profile(is_booster=True)], nick_displays={}, synced_at="2026-08-02T11:00:00+03:00"
    )
    sheets = _fake_sheets_client()
    service = _service(
        connection,
        roles=_FakeRoleGateway(),
        audit_gateway=_FakeAuditGateway(),
        audit_service=MagicMock(spec=AuditService),
        clock=_FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC)),
        sheets=sheets,
    )

    await service.sync_booster_flag(12345, True)

    sheets.batch_update.assert_not_called()


async def test_sync_booster_flag_is_a_no_op_when_discord_id_unbound(
    connection: aiosqlite.Connection,
) -> None:
    sheets = _fake_sheets_client()
    service = _service(
        connection,
        roles=_FakeRoleGateway(),
        audit_gateway=_FakeAuditGateway(),
        audit_service=MagicMock(spec=AuditService),
        clock=_FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC)),
        sheets=sheets,
    )

    await service.sync_booster_flag(99999, True)

    sheets.batch_update.assert_not_called()


async def test_manual_rank_role_is_left_untouched_by_the_poller(
    connection: aiosqlite.Connection,
) -> None:
    """PLAN.md §10.12: a rank granted via /set_rank must survive a background sync."""
    users = UsersCacheRepository(connection)
    state_repo = ProgressionStateRepository(connection)
    nick = NormalizedNick("scaryyyyy")
    clock = _FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))

    # Simulate /set_rank (M8) having already run: manual_rank_role=True.
    await users.replace_all(
        [_profile(rank="💎 Elite")], nick_displays={}, synced_at="2026-08-02T11:00:00+03:00"
    )
    await state_repo.upsert(
        ProgressionState(
            nick=nick,
            last_rank="💎 Elite",
            last_referral_role=None,
            announced_at=None,
            manual_rank_role=True,
        )
    )

    # The sheet now says a *different* rank — the poller must not react to it.
    await users.replace_all(
        [_profile(rank="🔹 Standard")], nick_displays={}, synced_at="2026-08-02T12:00:00+03:00"
    )
    roles = _FakeRoleGateway()
    audit_service = MagicMock(spec=AuditService)
    service = _service(
        connection,
        roles=roles,
        audit_gateway=_FakeAuditGateway(),
        audit_service=audit_service,
        clock=clock,
    )

    promotions = await service.sync([nick])

    assert promotions == []
    audit_service.record.assert_not_called()
    # The rank ladder's role ids are excluded from the sync entirely.
    (_member_id, role_set) = roles.calls[0]
    assert role_set.universe.isdisjoint(RankLadder().role_ids)

    state = await state_repo.get(nick)
    assert state is not None
    assert state.manual_rank_role is True  # preserved across the sync
