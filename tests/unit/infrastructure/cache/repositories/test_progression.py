"""Tests for `ProgressionRepository` against a real (temp-file) SQLite connection.

Exercises the §V.1 aggregation SQL end-to-end (deals -> aggregates ->
`compute_progression` -> stored snapshot) — the domain-level formula
itself is already proven exact against 250 real players in
`test_sheet_parity.py`; this file proves the SQL that feeds it.
"""

from datetime import UTC, datetime

import aiosqlite
import pytest_asyncio

from stalbot.domain.entities.coin_ledger import CoinLedgerEntry
from stalbot.domain.entities.deal import Deal
from stalbot.domain.enums import DealSource, DealType, OccurredAtKind
from stalbot.domain.money import Rub
from stalbot.domain.nick import NormalizedNick
from stalbot.infrastructure.cache.repositories.coin_ledger import CoinLedgerRepository
from stalbot.infrastructure.cache.repositories.deals import DealsRepository
from stalbot.infrastructure.cache.repositories.players import PlayersRepository
from stalbot.infrastructure.cache.repositories.progression import ProgressionRepository

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


def _deal(player_id: int, deal_type: DealType, amount: int) -> Deal:
    return Deal(
        id=None,
        player_id=player_id,
        occurred_at=NOW,
        occurred_at_kind=OccurredAtKind.BOT,
        deal_type=deal_type,
        amount=Rub(amount),
        coins=0,
        xp=0,
        rank_at_deal=None,
        booster_at_deal=False,
        recorded_by=None,
        source=DealSource.ADD,
        legacy_sheet_row=None,
        created_at=NOW,
    )


@pytest_asyncio.fixture
async def players(connection: aiosqlite.Connection) -> PlayersRepository:
    return PlayersRepository(connection)


async def test_aggregates_for_all_with_no_players_is_empty(
    connection: aiosqlite.Connection,
) -> None:
    repo = ProgressionRepository(connection)
    assert await repo.aggregates_for_all() == {}


async def test_aggregates_for_all_sums_own_turnover(
    connection: aiosqlite.Connection, players: PlayersRepository
) -> None:
    player = await players.get_or_create(NormalizedNick("scaryyyyy"), "Scaryyyyy", now=NOW)
    assert player.id is not None
    deals = DealsRepository(connection)
    await deals.insert(_deal(player.id, DealType.PURCHASE, 1_000_000))
    await deals.insert(_deal(player.id, DealType.SALE, 500_000))

    aggregates = await ProgressionRepository(connection).aggregates_for_all()

    agg = aggregates[player.id]
    assert agg.purchase_turnover == 1_000_000
    assert agg.sale_turnover == 500_000
    assert agg.has_referrer is False
    assert agg.referral_count == 0


async def test_aggregates_for_all_resolves_referrer_turnover(
    connection: aiosqlite.Connection, players: PlayersRepository
) -> None:
    referrer = await players.get_or_create(NormalizedNick("referrer"), "Referrer", now=NOW)
    referred = await players.get_or_create(NormalizedNick("referred"), "Referred", now=NOW)
    assert referrer.id is not None and referred.id is not None
    await players.set_referrer(referred.id, referrer.id, now=NOW)
    deals = DealsRepository(connection)
    await deals.insert(_deal(referred.id, DealType.SALE, 2_000_000))

    aggregates = await ProgressionRepository(connection).aggregates_for_all()

    referrer_agg = aggregates[referrer.id]
    assert referrer_agg.referral_count == 1
    assert referrer_agg.referee_total_turnover == 2_000_000
    referred_agg = aggregates[referred.id]
    assert referred_agg.has_referrer is True


async def test_aggregates_for_all_includes_coin_ledger_delta(
    connection: aiosqlite.Connection, players: PlayersRepository
) -> None:
    player = await players.get_or_create(NormalizedNick("scaryyyyy"), "Scaryyyyy", now=NOW)
    assert player.id is not None
    ledger = CoinLedgerRepository(connection)
    await ledger.add(
        CoinLedgerEntry(
            id=None, player_id=player.id, delta=-5, reason="spend", created_by=None, created_at=NOW
        )
    )

    aggregates = await ProgressionRepository(connection).aggregates_for_all()

    assert aggregates[player.id].coin_ledger_delta == -5


async def test_recompute_persists_and_reports_changed_players(
    connection: aiosqlite.Connection, players: PlayersRepository
) -> None:
    player = await players.get_or_create(NormalizedNick("scaryyyyy"), "Scaryyyyy", now=NOW)
    assert player.id is not None
    deals = DealsRepository(connection)
    await deals.insert(_deal(player.id, DealType.PURCHASE, 7_500_000))  # base_coins=5, base_xp=50

    repo = ProgressionRepository(connection)
    changed = await repo.recompute(now=NOW)

    assert changed == {player.id}
    stored = await repo.get(player.id)
    assert stored is not None
    # base_coins=5 (7.5M // 1.5M) + rank_one_time_coins=5 (reaching Standard
    # at xp=50 grants its one-time bonus, RANK_ONE_TIME_COINS["standard"]).
    assert stored.coins == 10
    assert stored.xp == 50
    assert stored.rank_key == "standard"
    assert stored.computed_at == NOW


async def test_recompute_is_a_no_op_when_nothing_changed(
    connection: aiosqlite.Connection, players: PlayersRepository
) -> None:
    player = await players.get_or_create(NormalizedNick("scaryyyyy"), "Scaryyyyy", now=NOW)
    assert player.id is not None
    await DealsRepository(connection).insert(_deal(player.id, DealType.PURCHASE, 1_500_000))
    repo = ProgressionRepository(connection)
    await repo.recompute(now=NOW)
    first = await repo.get(player.id)
    assert first is not None

    later = datetime(2026, 8, 11, tzinfo=UTC)
    changed = await repo.recompute(now=later)

    assert changed == set()
    second = await repo.get(player.id)
    assert second is not None
    assert second.computed_at == NOW  # untouched — recompute did not rewrite the row


async def test_recompute_scoped_to_specific_player_ids(
    connection: aiosqlite.Connection, players: PlayersRepository
) -> None:
    a = await players.get_or_create(NormalizedNick("a"), "A", now=NOW)
    b = await players.get_or_create(NormalizedNick("b"), "B", now=NOW)
    assert a.id is not None and b.id is not None
    deals = DealsRepository(connection)
    await deals.insert(_deal(a.id, DealType.PURCHASE, 1_500_000))
    await deals.insert(_deal(b.id, DealType.PURCHASE, 1_500_000))
    repo = ProgressionRepository(connection)

    changed = await repo.recompute([a.id], now=NOW)

    assert changed == {a.id}
    assert await repo.get(a.id) is not None
    assert await repo.get(b.id) is None


async def test_get_returns_none_when_never_computed(connection: aiosqlite.Connection) -> None:
    repo = ProgressionRepository(connection)
    assert await repo.get(1) is None
