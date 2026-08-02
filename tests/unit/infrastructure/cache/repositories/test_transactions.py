"""Tests for `TransactionsCacheRepository` against a real (temp-file) SQLite connection."""

from datetime import UTC, datetime
from decimal import Decimal

import aiosqlite

from stalbot.domain.entities.transaction import TransactionRecord
from stalbot.domain.entities.user_profile import UserProfile
from stalbot.domain.enums import DealType
from stalbot.domain.nick import NormalizedNick
from stalbot.infrastructure.cache.repositories.transactions import TransactionsCacheRepository
from stalbot.infrastructure.cache.repositories.users import UsersCacheRepository


def _record(row: int, nick: str = "scaryyyyy", **overrides: object) -> TransactionRecord:
    defaults: dict[str, object] = {
        "row": row,
        "at": datetime(2026, 7, 31, 21, 45, tzinfo=UTC),
        "nick": NormalizedNick(nick),
        "nick_display": nick,
        "deal_type": DealType.PURCHASE,
        "amount": Decimal(299900),
        "coins": 0,
        "xp": 0,
        "referrer": None,
    }
    defaults.update(overrides)
    return TransactionRecord(**defaults)  # type: ignore[arg-type]


async def test_upsert_many_then_last_row(connection: aiosqlite.Connection) -> None:
    repo = TransactionsCacheRepository(connection)
    await repo.upsert_many([_record(3), _record(4)])

    assert await repo.last_row() == 4


async def test_last_row_is_zero_when_empty(connection: aiosqlite.Connection) -> None:
    repo = TransactionsCacheRepository(connection)
    assert await repo.last_row() == 0


async def test_get_by_row_returns_the_matching_record(connection: aiosqlite.Connection) -> None:
    repo = TransactionsCacheRepository(connection)
    await repo.upsert_many([_record(3, amount=Decimal(100)), _record(4, amount=Decimal(200))])

    record = await repo.get_by_row(4)

    assert record is not None
    assert record.amount == Decimal(200)


async def test_get_by_row_returns_none_when_absent(connection: aiosqlite.Connection) -> None:
    repo = TransactionsCacheRepository(connection)
    assert await repo.get_by_row(999) is None


async def test_upsert_many_updates_existing_row(connection: aiosqlite.Connection) -> None:
    repo = TransactionsCacheRepository(connection)
    await repo.upsert_many([_record(3, amount=Decimal(100))])

    await repo.upsert_many([_record(3, amount=Decimal(200))])

    records = await repo.list_by_nick(NormalizedNick("scaryyyyy"))
    assert len(records) == 1
    assert records[0].amount == Decimal(200)


async def test_list_by_period_filters_inclusive(connection: aiosqlite.Connection) -> None:
    repo = TransactionsCacheRepository(connection)
    await repo.upsert_many(
        [
            _record(3, at=datetime(2026, 7, 30, 12, 0, tzinfo=UTC)),
            _record(4, at=datetime(2026, 7, 31, 12, 0, tzinfo=UTC)),
            _record(5, at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC)),
        ]
    )

    records = await repo.list_by_period(
        datetime(2026, 7, 31, 0, 0, tzinfo=UTC), datetime(2026, 7, 31, 23, 59, tzinfo=UTC)
    )

    assert [r.row for r in records] == [4]


async def test_display_nick_falls_back_to_normalized_when_user_absent(
    connection: aiosqlite.Connection,
) -> None:
    repo = TransactionsCacheRepository(connection)
    await repo.upsert_many([_record(3, nick="scaryyyyy", nick_display="Scaryyyyy")])

    record = (await repo.list_by_nick(NormalizedNick("scaryyyyy")))[0]

    assert record.nick_display == "scaryyyyy"


async def test_display_nick_resolves_from_users_table(
    connection: aiosqlite.Connection, synced_at: str
) -> None:
    users_repo = UsersCacheRepository(connection)
    await users_repo.replace_all(
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
            )
        ],
        nick_displays={NormalizedNick("scaryyyyy"): "Scaryyyyy"},
        synced_at=synced_at,
    )
    tx_repo = TransactionsCacheRepository(connection)
    await tx_repo.upsert_many([_record(3, nick="scaryyyyy")])

    record = (await tx_repo.list_by_nick(NormalizedNick("scaryyyyy")))[0]

    assert record.nick_display == "Scaryyyyy"


async def test_referrer_round_trips(connection: aiosqlite.Connection) -> None:
    repo = TransactionsCacheRepository(connection)
    await repo.upsert_many([_record(3, referrer=NormalizedNick("othernick"))])

    record = (await repo.list_by_nick(NormalizedNick("scaryyyyy")))[0]

    assert record.referrer == "othernick"
