"""Tests for `stalbot.application.services.coupons.CouponService` (заявка 26.08.2026)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import aiosqlite
import pytest

from stalbot.application.services.coupons import CouponService
from stalbot.domain.errors import (
    CouponAlreadyRedeemedError,
    CouponInactiveError,
    CouponNotFoundError,
)
from stalbot.infrastructure.cache.repositories.coupons import CouponsRepository
from tests.support.fake_clock import FakeClock

_NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)


def _service(connection: aiosqlite.Connection, *, now: datetime = _NOW) -> CouponService:
    return CouponService(CouponsRepository(connection), clock=FakeClock(now))


async def test_create_then_redeem(connection: aiosqlite.Connection) -> None:
    service = _service(connection)
    await service.create(
        "klondike10", Decimal("1.5"), max_uses=None, expires_at=None, created_by=1
    )

    coupon = await service.redeem("KLONDIKE10", channel_id=111, discord_id=222)

    assert coupon.code == "KLONDIKE10"
    assert coupon.discount_percent == Decimal("1.5")


async def test_code_lookup_is_case_insensitive(connection: aiosqlite.Connection) -> None:
    service = _service(connection)
    await service.create("SPRING", Decimal(5), max_uses=None, expires_at=None, created_by=1)

    coupon = await service.redeem("spring", channel_id=111, discord_id=222)

    assert coupon.code == "SPRING"


async def test_redeem_rejects_an_unknown_code(connection: aiosqlite.Connection) -> None:
    service = _service(connection)

    with pytest.raises(CouponNotFoundError):
        await service.redeem("GHOST", channel_id=111, discord_id=222)


async def test_redeem_rejects_a_disabled_coupon(connection: aiosqlite.Connection) -> None:
    service = _service(connection)
    await service.create("OLD", Decimal(5), max_uses=None, expires_at=None, created_by=1)
    await service.disable("OLD")

    with pytest.raises(CouponInactiveError):
        await service.redeem("OLD", channel_id=111, discord_id=222)


async def test_redeem_rejects_an_expired_coupon(connection: aiosqlite.Connection) -> None:
    service = _service(connection, now=_NOW)
    await service.create(
        "EXPIRING", Decimal(5), max_uses=None, expires_at=_NOW + timedelta(hours=1), created_by=1
    )
    later = _service(connection, now=_NOW + timedelta(hours=2))

    with pytest.raises(CouponInactiveError):
        await later.redeem("EXPIRING", channel_id=111, discord_id=222)


async def test_redeem_rejects_a_coupon_past_its_use_cap(connection: aiosqlite.Connection) -> None:
    service = _service(connection)
    await service.create("LIMITED", Decimal(5), max_uses=1, expires_at=None, created_by=1)
    await service.redeem("LIMITED", channel_id=111, discord_id=222)

    with pytest.raises(CouponInactiveError):
        await service.redeem("LIMITED", channel_id=112, discord_id=333)


async def test_redeem_rejects_the_same_account_twice(connection: aiosqlite.Connection) -> None:
    service = _service(connection)
    await service.create("ONCE", Decimal(5), max_uses=None, expires_at=None, created_by=1)
    await service.redeem("ONCE", channel_id=111, discord_id=222)

    with pytest.raises(CouponAlreadyRedeemedError):
        await service.redeem("ONCE", channel_id=112, discord_id=222)


async def test_different_accounts_can_each_redeem_once(connection: aiosqlite.Connection) -> None:
    service = _service(connection)
    await service.create("SHARE", Decimal(5), max_uses=None, expires_at=None, created_by=1)
    await service.redeem("SHARE", channel_id=111, discord_id=222)

    coupon = await service.redeem("SHARE", channel_id=112, discord_id=333)

    assert coupon.code == "SHARE"


async def test_disable_rejects_an_unknown_code(connection: aiosqlite.Connection) -> None:
    service = _service(connection)

    with pytest.raises(CouponNotFoundError):
        await service.disable("GHOST")
