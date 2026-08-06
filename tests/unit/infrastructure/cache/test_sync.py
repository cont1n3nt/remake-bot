"""Tests for `stalbot.infrastructure.cache.sync`.

Pure parsing helpers are tested directly; `CacheSync` orchestration is
tested against a fake `SheetsClient` (no network) and the real cache
repositories (real, temp-file SQLite via the `connection` fixture).
"""

from datetime import UTC, datetime
from decimal import Decimal

import aiosqlite
import pytest

from stalbot.domain.clock import GMT3
from stalbot.domain.enums import DealType, ItemCategory
from stalbot.domain.nick import NormalizedNick
from stalbot.infrastructure.cache import sync as sync_module
from stalbot.infrastructure.cache.repositories.items import ItemsCacheRepository
from stalbot.infrastructure.cache.repositories.transactions import TransactionsCacheRepository
from stalbot.infrastructure.cache.repositories.users import UsersCacheRepository
from stalbot.infrastructure.cache.sync import (
    LOW_FREE_ROWS_THRESHOLD,
    CacheSync,
    _parse_tickets,
    _parse_users,
    _to_decimal,
    _to_int,
    _to_int_strict,
    parse_items_block,
)

# --- pure helpers ------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), ("", None), ("123", Decimal(123)), (123, Decimal(123)), (123.0, Decimal(123))],
)
def test_to_decimal(value: object, expected: Decimal | None) -> None:
    assert _to_decimal(value) == expected


def test_to_decimal_returns_none_for_unparseable_text() -> None:
    assert _to_decimal("not a number") is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, 0), ("", 0), (True, 1), (False, 0), (123, 123), (123.9, 123), ("123", 123), ("abc", 0)],
)
def test_to_int(value: object, expected: int) -> None:
    assert _to_int(value) == expected


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), "nan", "inf"])
def test_to_int_treats_non_finite_values_as_unparseable_not_a_crash(value: object) -> None:
    """`int(nan)`/`int(inf)` raise (ValueError/OverflowError) instead of
    returning something — a non-finite cell must not escape uncaught."""
    assert _to_int(value) == 0


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), "nan", "inf"])
def test_to_int_strict_treats_non_finite_values_as_corrupt_not_a_crash(value: object) -> None:
    assert _to_int_strict(value) is None


def test_parse_tickets_skips_blank_rows() -> None:
    records, displays, skipped = _parse_tickets([["", "", False, False, ""]])
    assert records == []
    assert displays == {}
    assert skipped == 0


def test_parse_tickets_skips_row_with_unparseable_date() -> None:
    records, _displays, skipped = _parse_tickets([["", "Scaryyyyy", True, False, 100000, 0, 0, ""]])
    assert records == []
    assert skipped == 1


def test_parse_tickets_skips_row_with_both_flags_equal() -> None:
    records, _displays, skipped = _parse_tickets(
        [["31.07.2026 21:45", "Scaryyyyy", True, True, 100000, 0, 0, ""]]
    )
    assert records == []
    assert skipped == 1


def test_parse_tickets_parses_a_valid_purchase_row() -> None:
    records, displays, skipped = _parse_tickets(
        [["31.07.2026 21:45", "Scaryyyyy", True, False, 299900, 1, 10, "OtherNick"]]
    )
    assert skipped == 0
    (record,) = records
    assert record.row == 3
    assert record.nick == "scaryyyyy"
    assert record.deal_type is DealType.PURCHASE
    assert record.amount == Decimal(299900)
    assert record.coins == 1
    assert record.xp == 10
    assert record.referrer == "othernick"
    assert displays == {NormalizedNick("scaryyyyy"): "Scaryyyyy"}


def test_parse_tickets_parses_a_valid_sale_row_with_no_referrer() -> None:
    records, _displays, _skipped = _parse_tickets(
        [["31.07.2026 21:45", "Scaryyyyy", False, True, 50000, 0, 0, ""]]
    )
    (record,) = records
    assert record.deal_type is DealType.SALE
    assert record.referrer is None


def test_parse_tickets_row_numbers_follow_data_start_row() -> None:
    records, _displays, _skipped = _parse_tickets(
        [
            ["31.07.2026 21:45", "First", True, False, 1000, 0, 0, ""],
            ["31.07.2026 21:46", "Second", True, False, 2000, 0, 0, ""],
        ]
    )
    assert [r.row for r in records] == [3, 4]


def test_parse_users_skips_blank_nick_rows() -> None:
    profiles, warnings = _parse_users([["", "", 0, 0, 0, 0, 0, 0, False, "", ""]])
    assert profiles == []
    assert warnings == ()


def test_parse_users_parses_a_valid_row() -> None:
    profiles, warnings = _parse_users(
        [[123456, "dizzikss", 1, 0, 0, 1225100, 1225100, 1, True, "💎 Elite", "🧭 Скаут"]]
    )
    (profile,) = profiles
    assert profile.nick == "dizzikss"
    assert profile.discord_id == 123456
    assert profile.is_booster is True
    assert profile.rank == "💎 Elite"
    assert profile.referral_role == "🧭 Скаут"
    assert warnings == ()


def test_parse_users_blank_discord_id_becomes_none() -> None:
    (profile,) = _parse_users([["", "dizzikss", 0, 0, 0, 0, 0, 0, False, "", ""]])[0]
    assert profile.discord_id is None


def test_parse_users_blank_rank_becomes_none() -> None:
    (profile,) = _parse_users([["", "dizzikss", 0, 0, 0, 0, 0, 0, False, "", ""]])[0]
    assert profile.rank is None
    assert profile.referral_role is None


def test_parse_users_keeps_profile_with_unparseable_coins() -> None:
    profiles, warnings = _parse_users(
        [[123456, "dizzikss", "не число", 10, 0, 0, 0, 0, False, "", ""]]
    )
    (profile,) = profiles
    assert profile.coins == 0  # falls back like a genuinely empty cell...
    assert profile.nick == "dizzikss"  # ...but the profile itself is not dropped (INFRA1-4)
    assert len(warnings) == 1
    assert "coins" in warnings[0]


def test_parse_users_keeps_profile_with_unparseable_xp() -> None:
    profiles, warnings = _parse_users(
        [[123456, "dizzikss", 10, "не число", 0, 0, 0, 0, False, "", ""]]
    )
    (profile,) = profiles
    assert profile.xp == 0
    assert profile.coins == 10  # the unaffected sibling field parses normally
    assert len(warnings) == 1
    assert "xp" in warnings[0]


def test_parse_users_treats_text_false_as_false_not_truthy() -> None:
    """INFRA1-5: `bool("FALSE")` is `True` in plain Python — must not leak through."""
    (profile,) = _parse_users([[123456, "dizzikss", 0, 0, 0, 0, 0, 0, "FALSE", "", ""]])[0]
    assert profile.is_booster is False


def test_parse_tickets_skips_row_with_unparseable_coins() -> None:
    records, _displays, skipped = _parse_tickets(
        [["31.07.2026 21:45", "Scaryyyyy", True, False, 100000, "не число", 0, ""]]
    )
    assert records == []
    assert skipped == 1


def test_parse_tickets_treats_text_false_purchase_flag_as_false() -> None:
    """INFRA1-5: a manually typed `"FALSE"` cell must not read as truthy."""
    records, _displays, skipped = _parse_tickets(
        [["31.07.2026 21:45", "Scaryyyyy", "FALSE", "TRUE", 100000, 1, 10, ""]]
    )
    (record,) = records
    assert skipped == 0
    assert record.deal_type is DealType.SALE


def test_parse_items_block_skips_row_with_unknown_category(
    caplog: pytest.LogCaptureFixture,
) -> None:
    items = parse_items_block([[1, "Топот", "unknown_category", 250000, "", "topot", ""]])
    assert items == []


def test_parse_items_block_parses_a_valid_row() -> None:
    (item,) = parse_items_block(
        [[1, "Хвост тушкана", "resource", 18000, "", "tail", "29.07.2026 09:10"]]
    )
    assert item.id == 1
    assert item.name == "Хвост тушкана"
    assert item.category is ItemCategory.RESOURCE
    assert item.price_buy == Decimal(18000)
    assert item.price_sell is None
    assert item.emoji == "tail"
    assert item.updated_at == datetime(2026, 7, 29, 9, 10, tzinfo=GMT3)


def test_parse_items_block_skips_row_missing_id() -> None:
    assert parse_items_block([["", "Топот", "boost", "", 300000, "topot", ""]]) == []


# --- CacheSync orchestration --------------------------------------------


class _FakeSheetsClient:
    """Stands in for `SheetsClient` — no network, canned responses."""

    def __init__(
        self,
        *,
        tickets: list[list[object]] = [],  # noqa: B006 - test fixture data, never mutated
        users: list[list[object]] = [],  # noqa: B006
        items: list[list[object]] = [],  # noqa: B006
        formula_rows: int = 1000,
    ) -> None:
        self._data = {
            sync_module._TICKETS_RANGE: tickets,
            sync_module._USERS_RANGE: users,
            sync_module._ITEMS_RANGE: items,
        }
        self.formula_rows = formula_rows
        self.validate_calls = 0
        self.batch_get_calls: list[list[str]] = []

    async def validate_layout(self) -> None:
        self.validate_calls += 1

    async def batch_get(self, ranges: list[str]) -> dict[str, list[list[object]]]:
        self.batch_get_calls.append(list(ranges))
        return {ref: self._data[ref] for ref in ranges if self._data.get(ref)}

    async def read_formula_extent(self, ref: str) -> int:
        return self.formula_rows


class _FakeClock:
    def __init__(self, now: datetime) -> None:
        self.current = now

    def now(self) -> datetime:
        return self.current


def _cache_sync(
    connection: aiosqlite.Connection, client: _FakeSheetsClient, clock: _FakeClock
) -> CacheSync:
    return CacheSync(
        client,  # type: ignore[arg-type]
        items=ItemsCacheRepository(connection),
        users=UsersCacheRepository(connection),
        transactions=TransactionsCacheRepository(connection),
        clock=clock,
    )


_TICKET_ROW = ["31.07.2026 21:45", "Scaryyyyy", True, False, 299900, 1, 10, ""]
_USER_ROW = [123456, "scaryyyyy", 1, 10, 299900, 0, 299900, 0, False, "", ""]
_ITEM_ROW = [1, "Хвост тушкана", "resource", 18000, "", "tail", ""]


async def test_run_startup_sync_validates_then_populates_everything(
    connection: aiosqlite.Connection,
) -> None:
    client = _FakeSheetsClient(tickets=[_TICKET_ROW], users=[_USER_ROW], items=[_ITEM_ROW])
    clock = _FakeClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    cache_sync = _cache_sync(connection, client, clock)

    report = await cache_sync.run_startup_sync()

    assert client.validate_calls == 1
    assert report.items_synced == 1
    assert report.users_synced == 1
    assert report.transactions_synced == 1

    items_repo = ItemsCacheRepository(connection)
    users_repo = UsersCacheRepository(connection)
    assert len(await items_repo.all()) == 1
    assert await users_repo.get_by_nick(NormalizedNick("scaryyyyy")) is not None


async def test_sync_items_does_not_touch_users_or_transactions(
    connection: aiosqlite.Connection,
) -> None:
    client = _FakeSheetsClient(tickets=[_TICKET_ROW], users=[_USER_ROW], items=[_ITEM_ROW])
    clock = _FakeClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    cache_sync = _cache_sync(connection, client, clock)

    report = await cache_sync.sync_items()

    assert report.items_synced == 1
    users_repo = UsersCacheRepository(connection)
    assert await users_repo.all() == []


async def test_sync_users_and_transactions_costs_two_api_calls(
    connection: aiosqlite.Connection,
) -> None:
    client = _FakeSheetsClient(tickets=[_TICKET_ROW], users=[_USER_ROW])
    clock = _FakeClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    cache_sync = _cache_sync(connection, client, clock)

    report = await cache_sync.sync_users_and_transactions()

    assert report.api_calls == 2
    assert len(client.batch_get_calls) == 1  # the formula-extent read is a separate call type


async def test_low_formula_extent_produces_a_warning(connection: aiosqlite.Connection) -> None:
    client = _FakeSheetsClient(tickets=[_TICKET_ROW], users=[_USER_ROW], formula_rows=1)
    clock = _FakeClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    cache_sync = _cache_sync(connection, client, clock)

    report = await cache_sync.sync_users_and_transactions()

    assert report.formula_free_rows is not None
    assert report.formula_free_rows < LOW_FREE_ROWS_THRESHOLD
    assert report.warnings
    assert "⚠️" in report.warnings[0]


async def test_healthy_formula_extent_produces_no_warning(connection: aiosqlite.Connection) -> None:
    client = _FakeSheetsClient(tickets=[_TICKET_ROW], users=[_USER_ROW], formula_rows=1000)
    clock = _FakeClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    cache_sync = _cache_sync(connection, client, clock)

    report = await cache_sync.sync_users_and_transactions()

    assert report.warnings == ()


async def test_ensure_fresh_skips_when_recently_synced(connection: aiosqlite.Connection) -> None:
    client = _FakeSheetsClient(tickets=[_TICKET_ROW], users=[_USER_ROW])
    clock = _FakeClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    cache_sync = _cache_sync(connection, client, clock)
    await cache_sync.sync_users_and_transactions()
    calls_before = len(client.batch_get_calls)

    refreshed = await cache_sync.ensure_fresh(max_age_seconds=3600)

    assert refreshed is False
    assert len(client.batch_get_calls) == calls_before


async def test_ensure_fresh_refreshes_when_stale(connection: aiosqlite.Connection) -> None:
    client = _FakeSheetsClient(tickets=[_TICKET_ROW], users=[_USER_ROW])
    clock = _FakeClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    cache_sync = _cache_sync(connection, client, clock)
    await cache_sync.sync_users_and_transactions()
    calls_before = len(client.batch_get_calls)
    clock.current = datetime(2026, 8, 2, 13, 0, tzinfo=UTC)  # +1h

    refreshed = await cache_sync.ensure_fresh(max_age_seconds=60)

    assert refreshed is True
    assert len(client.batch_get_calls) == calls_before + 1


async def test_ensure_fresh_refreshes_when_cache_never_synced(
    connection: aiosqlite.Connection,
) -> None:
    client = _FakeSheetsClient(tickets=[_TICKET_ROW], users=[_USER_ROW])
    clock = _FakeClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    cache_sync = _cache_sync(connection, client, clock)

    refreshed = await cache_sync.ensure_fresh(max_age_seconds=3600)

    assert refreshed is True


async def test_cache_hit_rate_is_none_before_any_ensure_fresh_call(
    connection: aiosqlite.Connection,
) -> None:
    client = _FakeSheetsClient()
    clock = _FakeClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    cache_sync = _cache_sync(connection, client, clock)

    assert cache_sync.cache_hit_rate is None


async def test_cache_hit_rate_reflects_fresh_vs_stale_calls(
    connection: aiosqlite.Connection,
) -> None:
    client = _FakeSheetsClient(tickets=[_TICKET_ROW], users=[_USER_ROW])
    clock = _FakeClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    cache_sync = _cache_sync(connection, client, clock)
    await cache_sync.ensure_fresh(max_age_seconds=3600)  # stale (never synced)
    await cache_sync.ensure_fresh(max_age_seconds=3600)  # fresh
    await cache_sync.ensure_fresh(max_age_seconds=3600)  # fresh

    assert cache_sync.cache_hit_rate == 2 / 3


async def test_last_reports_are_none_before_any_sync(connection: aiosqlite.Connection) -> None:
    client = _FakeSheetsClient()
    clock = _FakeClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    cache_sync = _cache_sync(connection, client, clock)

    assert cache_sync.last_users_report is None
    assert cache_sync.last_items_report is None


async def test_last_reports_are_stored_after_each_sync(connection: aiosqlite.Connection) -> None:
    client = _FakeSheetsClient(tickets=[_TICKET_ROW], users=[_USER_ROW], items=[_ITEM_ROW])
    clock = _FakeClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC))
    cache_sync = _cache_sync(connection, client, clock)

    users_report = await cache_sync.sync_users_and_transactions()
    items_report = await cache_sync.sync_items()

    assert cache_sync.last_users_report == users_report
    assert cache_sync.last_items_report == items_report
