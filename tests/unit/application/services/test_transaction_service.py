"""Tests for `stalbot.application.services.transactions.TransactionService`.

PLAN.md §7.4, §10.1; sqlite_migration.md Э7. The cache repositories are
real, SQLite-backed, for genuine round-trip confidence — there is no more
Sheets client to mock, `register()` is now pure database + pure-function
orchestration.
"""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import aiosqlite

from stalbot.application.dto.transaction_request import AddTransactionRequest
from stalbot.application.services.transactions import TransactionService
from stalbot.domain.entities.player_progression import PlayerProgressionRecord
from stalbot.domain.enums import DealSource, DealType, OccurredAtKind
from stalbot.domain.nick import NormalizedNick
from stalbot.domain.progression.calculator import deal_reward
from stalbot.infrastructure.cache.repositories.deals import DealsRepository
from stalbot.infrastructure.cache.repositories.idempotency import IdempotencyRepository
from stalbot.infrastructure.cache.repositories.players import PlayersRepository
from stalbot.infrastructure.cache.repositories.progression import ProgressionRepository
from tests.support.fake_clock import FakeClock


def _service(connection: aiosqlite.Connection, *, clock: FakeClock) -> TransactionService:
    return TransactionService(
        PlayersRepository(connection),
        DealsRepository(connection),
        ProgressionRepository(connection),
        IdempotencyRepository(connection),
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


async def test_register_inserts_a_deal_with_the_computed_reward(
    connection: aiosqlite.Connection,
) -> None:
    clock = FakeClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service = _service(connection, clock=clock)

    result = await service.register(_request())

    expected = deal_reward(DealType.PURCHASE, 299900)
    assert result.deal.id is not None
    assert result.deal.amount == 299900
    assert result.deal.coins == expected.coins
    assert result.deal.xp == expected.xp
    assert result.deal.deal_type is DealType.PURCHASE
    assert result.deal.occurred_at == clock.now()
    assert result.deal.occurred_at_kind is OccurredAtKind.BOT
    assert result.deal.source is DealSource.ADD
    assert result.deal.recorded_by == 12345
    assert result.nick_display == "Scaryyyyy"
    assert result.replayed is False

    stored = await DealsRepository(connection).get_by_id(result.deal.id)
    assert stored == result.deal


async def test_register_ticket_source_is_recorded(connection: aiosqlite.Connection) -> None:
    clock = FakeClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service = _service(connection, clock=clock)

    result = await service.register(_request(source=DealSource.TICKET))

    assert result.deal.source is DealSource.TICKET


async def test_register_rounds_fractional_amount_consistently(
    connection: aiosqlite.Connection,
) -> None:
    clock = FakeClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service = _service(connection, clock=clock)

    result = await service.register(_request(amount=Decimal("150.5")))

    assert result.deal.amount == 151  # ROUND_HALF_UP, not truncation


async def test_register_snapshots_rank_and_booster_at_deal_time(
    connection: aiosqlite.Connection,
) -> None:
    """A deal freezes the player's rank/booster status as it was *before* this
    deal — later rank changes must not retroactively alter it."""
    now = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
    clock = FakeClock(now)
    players = PlayersRepository(connection)
    progression = ProgressionRepository(connection)

    player = await players.get_or_create(NormalizedNick("scaryyyyy"), "Scaryyyyy", now=now)
    assert player.id is not None
    await players.set_booster(player.id, True, now=now)
    await progression.upsert(
        PlayerProgressionRecord(
            player_id=player.id,
            purchase_turnover=0,
            sale_turnover=0,
            total_turnover=0,
            referral_count=0,
            coins=0,
            xp=0,
            rank_key="elite",
            referral_role_key=None,
            breakdown_json="{}",
            calculator_version=1,
            computed_at=now,
        )
    )

    service = _service(connection, clock=clock)
    result = await service.register(_request())

    assert result.deal.rank_at_deal == "elite"
    assert result.deal.booster_at_deal is True


async def test_register_is_idempotent_for_the_same_key(connection: aiosqlite.Connection) -> None:
    clock = FakeClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service = _service(connection, clock=clock)
    request = _request()

    first = await service.register(request)
    second = await service.register(request)

    assert second.deal.id == first.deal.id
    assert second.deal.amount == first.deal.amount
    assert second.deal.coins == first.deal.coins
    assert second.discord_bound is False
    assert second.replayed is True

    deals = await DealsRepository(connection).count()
    assert deals == 1  # the replay must not have inserted a second deal


async def test_register_serializes_concurrent_calls_with_the_same_idempotency_key(
    connection: aiosqlite.Connection,
) -> None:
    """CLUSTER-1: two concurrent registrations for the same deal must not both write."""
    clock = FakeClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service = _service(connection, clock=clock)
    request = _request()

    first, second = await asyncio.gather(service.register(request), service.register(request))

    assert first.deal.id == second.deal.id
    deals = await DealsRepository(connection).count()
    assert deals == 1


async def test_register_writes_referrer_only_on_the_first_deal(
    connection: aiosqlite.Connection,
) -> None:
    clock = FakeClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service = _service(connection, clock=clock)
    players = PlayersRepository(connection)

    await service.register(_request(idempotency_key="i1", referrer_nick="OtherNick"))
    player = await players.get_by_nick(NormalizedNick("scaryyyyy"))
    assert player is not None and player.referrer_player_id is not None
    referrer = await players.get_by_id(player.referrer_player_id)
    assert referrer is not None
    assert referrer.nick_norm == NormalizedNick("othernick")

    await service.register(
        _request(idempotency_key="i2", referrer_nick="YetAnotherNick", amount=Decimal(1000))
    )
    unchanged = await players.get_by_nick(NormalizedNick("scaryyyyy"))
    assert unchanged is not None
    assert unchanged.referrer_player_id == player.referrer_player_id  # still the first referrer


async def test_register_recomputes_progression_for_player_and_referrer(
    connection: aiosqlite.Connection,
) -> None:
    clock = FakeClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service = _service(connection, clock=clock)
    players = PlayersRepository(connection)
    progression = ProgressionRepository(connection)

    await service.register(_request(referrer_nick="OtherNick"))

    player = await players.get_by_nick(NormalizedNick("scaryyyyy"))
    referrer = await players.get_by_nick(NormalizedNick("othernick"))
    assert player is not None and referrer is not None
    assert await progression.get(player.id) is not None  # type: ignore[arg-type]
    assert await progression.get(referrer.id) is not None  # type: ignore[arg-type]


async def test_register_binds_an_unbound_discord_id(connection: aiosqlite.Connection) -> None:
    clock = FakeClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service = _service(connection, clock=clock)

    result = await service.register(_request(discord_id=999))

    assert result.discord_bound is True
    player = await PlayersRepository(connection).get_by_nick(NormalizedNick("scaryyyyy"))
    assert player is not None
    assert player.discord_id == 999


async def test_register_does_not_rebind_without_force(connection: aiosqlite.Connection) -> None:
    clock = FakeClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service = _service(connection, clock=clock)
    await service.register(_request(idempotency_key="i1", discord_id=111))

    result = await service.register(
        _request(idempotency_key="i2", discord_id=999, force_rebind=False)
    )

    assert result.discord_bound is False
    player = await PlayersRepository(connection).get_by_nick(NormalizedNick("scaryyyyy"))
    assert player is not None
    assert player.discord_id == 111


async def test_register_rebinds_when_forced(connection: aiosqlite.Connection) -> None:
    clock = FakeClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    service = _service(connection, clock=clock)
    await service.register(_request(idempotency_key="i1", discord_id=111))

    result = await service.register(
        _request(idempotency_key="i2", discord_id=999, force_rebind=True)
    )

    assert result.discord_bound is True
    player = await PlayersRepository(connection).get_by_nick(NormalizedNick("scaryyyyy"))
    assert player is not None
    assert player.discord_id == 999
