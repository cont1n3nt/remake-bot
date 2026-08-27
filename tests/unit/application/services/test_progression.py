"""Tests for `stalbot.application.services.progression.ProgressionService` (PLAN.md §9.2).

`PlayersRepository`/`ProgressionRepository`/`ProgressionStateRepository` are
real, SQLite-backed (genuine round-trip confidence); `RoleGateway`/
`AuditGateway` are fakes (they are `Protocol` ports, built exactly for
this); `AuditService` is a `MagicMock` since it is a concrete class whose
own behavior is already covered by `test_audit.py`.

sqlite_migration.md Э6: the service now reads `players`/`player_progression`
(ladder *keys*, not sheet-label text) instead of the sheet-era `users`
cache — this file drives that data through `PlayersRepository.get_or_create`
+ `ProgressionRepository.upsert` directly rather than `UsersCacheRepository.
replace_all`.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import aiosqlite

from stalbot.application.dto.progression_state import ProgressionState
from stalbot.application.services.audit import AuditService
from stalbot.application.services.progression import ProgressionService
from stalbot.domain.entities.player_progression import PlayerProgressionRecord
from stalbot.domain.nick import NormalizedNick
from stalbot.domain.progression.ranks import RankLadder
from stalbot.infrastructure.cache.repositories.players import PlayersRepository
from stalbot.infrastructure.cache.repositories.progression import ProgressionRepository
from stalbot.infrastructure.cache.repositories.progression_state import ProgressionStateRepository
from stalbot.presentation.embeds.factory import EmbedFactory
from tests.support.fake_clock import FakeClock
from tests.support.fakes import FakeAuditGateway, FakeChannel, FakeRoleGateway


async def _seed_player(
    connection: aiosqlite.Connection,
    nick: str = "scaryyyyy",
    *,
    discord_id: int | None = 12345,
    is_booster: bool = False,
    rank_key: str | None = "elite",
    referral_role_key: str | None = None,
    coins: int = 1240,
    xp: int = 3780,
    referral_count: int = 0,
    now: datetime = datetime(2026, 8, 2, 12, 0, tzinfo=UTC),
) -> int:
    """Create a player (with a Discord id already bound) and its progression record."""
    players = PlayersRepository(connection)
    player = await players.get_or_create(NormalizedNick(nick), nick.capitalize(), now=now)
    assert player.id is not None
    if discord_id is not None:
        await players.set_discord_id(player.id, discord_id, now=now)
    if is_booster:
        await players.set_booster(player.id, True, now=now)
    await ProgressionRepository(connection).upsert(
        PlayerProgressionRecord(
            player_id=player.id,
            purchase_turnover=0,
            sale_turnover=0,
            total_turnover=0,
            referral_count=referral_count,
            coins=coins,
            xp=xp,
            rank_key=rank_key,
            referral_role_key=referral_role_key,
            breakdown_json="{}",
            calculator_version=1,
            computed_at=now,
        )
    )
    return player.id


def _service(
    connection: aiosqlite.Connection,
    *,
    roles: FakeRoleGateway,
    audit_gateway: FakeAuditGateway,
    audit_service: MagicMock,
    clock: FakeClock,
) -> ProgressionService:
    return ProgressionService(
        PlayersRepository(connection),
        ProgressionRepository(connection),
        ProgressionStateRepository(connection),
        roles,
        audit_gateway,  # type: ignore[arg-type]
        audit_service,
        EmbedFactory(),
        clock=clock,
    )


async def test_player_without_discord_id_is_skipped(connection: aiosqlite.Connection) -> None:
    await _seed_player(connection, discord_id=None)
    roles = FakeRoleGateway()
    service = _service(
        connection,
        roles=roles,
        audit_gateway=FakeAuditGateway(),
        audit_service=MagicMock(spec=AuditService),
        clock=FakeClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC)),
    )

    promotions = await service.sync([NormalizedNick("scaryyyyy")])

    assert promotions == []
    assert roles.calls == []


async def test_first_ever_sync_records_baseline_without_announcing(
    connection: aiosqlite.Connection,
) -> None:
    await _seed_player(connection)
    roles = FakeRoleGateway()
    audit_gateway = FakeAuditGateway()
    audit_service = MagicMock(spec=AuditService)
    service = _service(
        connection,
        roles=roles,
        audit_gateway=audit_gateway,
        audit_service=audit_service,
        clock=FakeClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC)),
    )

    promotions = await service.sync([NormalizedNick("scaryyyyy")])

    assert promotions == []
    assert len(roles.calls) == 1  # roles are still reconciled on first sync
    assert audit_gateway.batches == []
    audit_service.record.assert_not_called()

    state = await ProgressionStateRepository(connection).get(NormalizedNick("scaryyyyy"))
    assert state is not None
    assert state.last_rank == "elite"


async def test_genuine_rank_promotion_is_announced_and_role_switched(
    connection: aiosqlite.Connection,
) -> None:
    clock = FakeClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))

    # Baseline: player was already tracked at Prestige.
    await _seed_player(connection, rank_key="prestige")
    roles = FakeRoleGateway()
    audit_gateway = FakeAuditGateway()
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

    # Now the calculator says Elite.
    await _seed_player(connection, rank_key="elite")
    channel = FakeChannel()

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
    clock = FakeClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    roles = FakeRoleGateway()
    audit_gateway = FakeAuditGateway()
    audit_service = MagicMock(spec=AuditService)
    service = _service(
        connection,
        roles=roles,
        audit_gateway=audit_gateway,
        audit_service=audit_service,
        clock=clock,
    )

    await _seed_player(connection, rank_key="elite")
    await service.sync([NormalizedNick("scaryyyyy")])

    await _seed_player(connection, rank_key="prestige")
    promotions = await service.sync([NormalizedNick("scaryyyyy")])

    assert promotions == []
    audit_service.record.assert_not_called()


async def test_no_change_still_reconciles_roles_but_does_not_announce(
    connection: aiosqlite.Connection,
) -> None:
    clock = FakeClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    roles = FakeRoleGateway()
    audit_service = MagicMock(spec=AuditService)
    service = _service(
        connection,
        roles=roles,
        audit_gateway=FakeAuditGateway(),
        audit_service=audit_service,
        clock=clock,
    )

    await _seed_player(connection)
    await service.sync([NormalizedNick("scaryyyyy")])
    promotions = await service.sync([NormalizedNick("scaryyyyy")])

    assert promotions == []
    assert len(roles.calls) == 2
    audit_service.record.assert_not_called()


async def test_announce_to_none_falls_back_to_audit_gateway(
    connection: aiosqlite.Connection,
) -> None:
    clock = FakeClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    audit_gateway = FakeAuditGateway()
    service = _service(
        connection,
        roles=FakeRoleGateway(),
        audit_gateway=audit_gateway,
        audit_service=MagicMock(spec=AuditService),
        clock=clock,
    )

    await _seed_player(connection, rank_key="prestige")
    await service.sync([NormalizedNick("scaryyyyy")])
    await _seed_player(connection, rank_key="elite")

    promotions = await service.sync([NormalizedNick("scaryyyyy")])  # announce_to defaults to None

    assert len(promotions) == 1
    assert len(audit_gateway.batches) == 1


async def test_sync_with_no_nicks_covers_the_whole_player_base(
    connection: aiosqlite.Connection,
) -> None:
    await _seed_player(connection, "first", discord_id=11111)
    await _seed_player(connection, "second", discord_id=54321)
    roles = FakeRoleGateway()
    service = _service(
        connection,
        roles=roles,
        audit_gateway=FakeAuditGateway(),
        audit_service=MagicMock(spec=AuditService),
        clock=FakeClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC)),
    )

    await service.sync()

    assert len(roles.calls) == 2


async def test_unrecognized_rank_key_is_not_included_in_desired_roles(
    connection: aiosqlite.Connection,
) -> None:
    await _seed_player(connection, rank_key="not_a_real_rank")
    roles = FakeRoleGateway()
    service = _service(
        connection,
        roles=roles,
        audit_gateway=FakeAuditGateway(),
        audit_service=MagicMock(spec=AuditService),
        clock=FakeClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC)),
    )

    await service.sync([NormalizedNick("scaryyyyy")])

    assert roles.calls[0][1].desired == frozenset()


async def test_sync_booster_flag_updates_player_and_recomputes_progression(
    connection: aiosqlite.Connection,
) -> None:
    await _seed_player(connection, is_booster=False)
    roles = FakeRoleGateway()
    service = _service(
        connection,
        roles=roles,
        audit_gateway=FakeAuditGateway(),
        audit_service=MagicMock(spec=AuditService),
        clock=FakeClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC)),
    )

    await service.sync_booster_flag(12345, True)

    updated = await PlayersRepository(connection).get_by_nick(NormalizedNick("scaryyyyy"))
    assert updated is not None
    assert updated.is_booster is True
    assert len(roles.calls) == 1  # sync_booster_flag also resyncs progression


async def test_sync_booster_flag_is_a_no_op_when_already_correct(
    connection: aiosqlite.Connection,
) -> None:
    await _seed_player(connection, is_booster=True)
    roles = FakeRoleGateway()
    service = _service(
        connection,
        roles=roles,
        audit_gateway=FakeAuditGateway(),
        audit_service=MagicMock(spec=AuditService),
        clock=FakeClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC)),
    )

    await service.sync_booster_flag(12345, True)

    assert roles.calls == []  # nothing recomputed or resynced


async def test_sync_booster_flag_is_a_no_op_when_discord_id_unbound(
    connection: aiosqlite.Connection,
) -> None:
    roles = FakeRoleGateway()
    service = _service(
        connection,
        roles=roles,
        audit_gateway=FakeAuditGateway(),
        audit_service=MagicMock(spec=AuditService),
        clock=FakeClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC)),
    )

    await service.sync_booster_flag(99999, True)

    assert roles.calls == []


async def test_manual_rank_role_is_left_untouched_by_the_poller(
    connection: aiosqlite.Connection,
) -> None:
    """PLAN.md §10.12: a rank granted via /set_rank must survive a background sync."""
    nick = NormalizedNick("scaryyyyy")
    clock = FakeClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))

    # Simulate /set_rank (M8) having already run: manual_rank_role=True.
    await _seed_player(connection, rank_key="elite")
    state_repo = ProgressionStateRepository(connection)
    await state_repo.upsert(
        ProgressionState(
            nick=nick,
            last_rank="elite",
            last_referral_role=None,
            announced_at=None,
            manual_rank_role=True,
        )
    )

    # The calculator now says a *different* rank — the poller must not react to it.
    await _seed_player(connection, rank_key="standard")
    roles = FakeRoleGateway()
    audit_service = MagicMock(spec=AuditService)
    service = _service(
        connection,
        roles=roles,
        audit_gateway=FakeAuditGateway(),
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


# -- resync_all (заявка 27.08.2026: on-demand full resync) -----------------


async def test_resync_all_skips_players_without_a_discord_id(
    connection: aiosqlite.Connection,
) -> None:
    await _seed_player(connection, discord_id=None)
    roles = FakeRoleGateway()
    service = _service(
        connection,
        roles=roles,
        audit_gateway=FakeAuditGateway(),
        audit_service=MagicMock(spec=AuditService),
        clock=FakeClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC)),
    )

    changes = await service.resync_all()

    assert changes == []
    assert roles.calls == []


async def test_resync_all_skips_players_with_no_ladder_role(
    connection: aiosqlite.Connection,
) -> None:
    await _seed_player(connection, rank_key=None, referral_role_key=None)
    roles = FakeRoleGateway()
    service = _service(
        connection,
        roles=roles,
        audit_gateway=FakeAuditGateway(),
        audit_service=MagicMock(spec=AuditService),
        clock=FakeClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC)),
    )

    changes = await service.resync_all()

    assert changes == []


async def test_resync_all_reports_the_roles_actually_changed(
    connection: aiosqlite.Connection,
) -> None:
    await _seed_player(connection, "first", discord_id=11111, rank_key="elite")
    await _seed_player(connection, "second", discord_id=22222, rank_key=None)
    roles = FakeRoleGateway()
    service = _service(
        connection,
        roles=roles,
        audit_gateway=FakeAuditGateway(),
        audit_service=MagicMock(spec=AuditService),
        clock=FakeClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC)),
    )

    changes = await service.resync_all()

    assert len(changes) == 1
    assert changes[0].nick == NormalizedNick("first")
    assert changes[0].discord_id == 11111
    elite = RankLadder().by_key("elite")
    assert elite is not None
    assert changes[0].granted == (elite.role_id,)
    assert changes[0].revoked == ()
