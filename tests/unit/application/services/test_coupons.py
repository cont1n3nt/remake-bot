"""Tests for `stalbot.application.services.coupons.CouponService` (заявка 26.08+27.08.2026)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import aiosqlite
import pytest

from stalbot.application.services.coupons import CouponService
from stalbot.domain.enums import CouponKind, TicketKind
from stalbot.domain.errors import (
    CouponAlreadyRedeemedError,
    CouponInactiveError,
    CouponNotFoundError,
    CouponWrongKindError,
)
from stalbot.infrastructure.cache.repositories.coupons import CouponsRepository
from tests.support.fake_clock import FakeClock

_NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)


def _service(connection: aiosqlite.Connection, *, now: datetime = _NOW) -> CouponService:
    return CouponService(CouponsRepository(connection), clock=FakeClock(now))


async def test_create_then_redeem(connection: aiosqlite.Connection) -> None:
    service = _service(connection)
    await service.create(
        "klondike10",
        CouponKind.DISCOUNT,
        Decimal("1.5"),
        max_uses=None,
        expires_at=None,
        created_by=1,
    )

    coupon = await service.redeem(
        "KLONDIKE10", channel_id=111, discord_id=222, ticket_kind=TicketKind.ORDER_BOOSTS
    )

    assert coupon.code == "KLONDIKE10"
    assert coupon.kind is CouponKind.DISCOUNT
    assert coupon.discount_percent == Decimal("1.5")


async def test_code_lookup_is_case_insensitive(connection: aiosqlite.Connection) -> None:
    service = _service(connection)
    await service.create(
        "SPRING", CouponKind.DISCOUNT, Decimal(5), max_uses=None, expires_at=None, created_by=1
    )

    coupon = await service.redeem(
        "spring", channel_id=111, discord_id=222, ticket_kind=TicketKind.ORDER_BOOSTS
    )

    assert coupon.code == "SPRING"


async def test_redeem_rejects_an_unknown_code(connection: aiosqlite.Connection) -> None:
    service = _service(connection)

    with pytest.raises(CouponNotFoundError):
        await service.redeem(
            "GHOST", channel_id=111, discord_id=222, ticket_kind=TicketKind.ORDER_BOOSTS
        )


async def test_redeem_rejects_a_disabled_coupon(connection: aiosqlite.Connection) -> None:
    service = _service(connection)
    await service.create(
        "OLD", CouponKind.DISCOUNT, Decimal(5), max_uses=None, expires_at=None, created_by=1
    )
    await service.disable("OLD")

    with pytest.raises(CouponInactiveError):
        await service.redeem(
            "OLD", channel_id=111, discord_id=222, ticket_kind=TicketKind.ORDER_BOOSTS
        )


async def test_redeem_rejects_an_expired_coupon(connection: aiosqlite.Connection) -> None:
    service = _service(connection, now=_NOW)
    await service.create(
        "EXPIRING",
        CouponKind.DISCOUNT,
        Decimal(5),
        max_uses=None,
        expires_at=_NOW + timedelta(hours=1),
        created_by=1,
    )
    later = _service(connection, now=_NOW + timedelta(hours=2))

    with pytest.raises(CouponInactiveError):
        await later.redeem(
            "EXPIRING", channel_id=111, discord_id=222, ticket_kind=TicketKind.ORDER_BOOSTS
        )


async def test_redeem_rejects_a_coupon_past_its_use_cap(connection: aiosqlite.Connection) -> None:
    service = _service(connection)
    await service.create(
        "LIMITED", CouponKind.DISCOUNT, Decimal(5), max_uses=1, expires_at=None, created_by=1
    )
    await service.redeem(
        "LIMITED", channel_id=111, discord_id=222, ticket_kind=TicketKind.ORDER_BOOSTS
    )

    with pytest.raises(CouponInactiveError):
        await service.redeem(
            "LIMITED", channel_id=112, discord_id=333, ticket_kind=TicketKind.ORDER_BOOSTS
        )


async def test_redeem_rejects_the_same_account_twice(connection: aiosqlite.Connection) -> None:
    service = _service(connection)
    await service.create(
        "ONCE", CouponKind.DISCOUNT, Decimal(5), max_uses=None, expires_at=None, created_by=1
    )
    await service.redeem(
        "ONCE", channel_id=111, discord_id=222, ticket_kind=TicketKind.ORDER_BOOSTS
    )

    with pytest.raises(CouponAlreadyRedeemedError):
        await service.redeem(
            "ONCE", channel_id=112, discord_id=222, ticket_kind=TicketKind.ORDER_BOOSTS
        )


async def test_different_accounts_can_each_redeem_once(connection: aiosqlite.Connection) -> None:
    service = _service(connection)
    await service.create(
        "SHARE", CouponKind.DISCOUNT, Decimal(5), max_uses=None, expires_at=None, created_by=1
    )
    await service.redeem(
        "SHARE", channel_id=111, discord_id=222, ticket_kind=TicketKind.ORDER_BOOSTS
    )

    coupon = await service.redeem(
        "SHARE", channel_id=112, discord_id=333, ticket_kind=TicketKind.ORDER_BOOSTS
    )

    assert coupon.code == "SHARE"


async def test_disable_rejects_an_unknown_code(connection: aiosqlite.Connection) -> None:
    service = _service(connection)

    with pytest.raises(CouponNotFoundError):
        await service.disable("GHOST")


# -- заявка 27.08.2026 п.10: discount/markup, scoped to the right ticket kind ---


async def test_discount_coupon_rejected_outside_order_boosts(
    connection: aiosqlite.Connection,
) -> None:
    service = _service(connection)
    await service.create(
        "SALE", CouponKind.DISCOUNT, Decimal(5), max_uses=None, expires_at=None, created_by=1
    )

    with pytest.raises(CouponWrongKindError):
        await service.redeem(
            "SALE", channel_id=111, discord_id=222, ticket_kind=TicketKind.SELL_ITEMS
        )


async def test_markup_coupon_rejected_on_order_boosts(connection: aiosqlite.Connection) -> None:
    service = _service(connection)
    await service.create(
        "SKUP", CouponKind.MARKUP, Decimal(5), max_uses=None, expires_at=None, created_by=1
    )

    with pytest.raises(CouponWrongKindError):
        await service.redeem(
            "SKUP", channel_id=111, discord_id=222, ticket_kind=TicketKind.ORDER_BOOSTS
        )


async def test_markup_coupon_accepted_on_either_sell_kind(
    connection: aiosqlite.Connection,
) -> None:
    service = _service(connection)
    await service.create(
        "SKUP", CouponKind.MARKUP, Decimal(5), max_uses=None, expires_at=None, created_by=1
    )

    await service.redeem(
        "SKUP", channel_id=111, discord_id=222, ticket_kind=TicketKind.SELL_ITEMS
    )
    coupon = await service.redeem(
        "SKUP", channel_id=112, discord_id=333, ticket_kind=TicketKind.SELL_BOOSTS
    )

    assert coupon.code == "SKUP"


# -- /coupons CRUD (заявка 27.08.2026 п.4) --------------------------------


async def test_list_active_omits_disabled_coupons(connection: aiosqlite.Connection) -> None:
    service = _service(connection)
    await service.create(
        "LIVE", CouponKind.DISCOUNT, Decimal(5), max_uses=None, expires_at=None, created_by=1
    )
    await service.create(
        "DEAD", CouponKind.DISCOUNT, Decimal(5), max_uses=None, expires_at=None, created_by=1
    )
    await service.disable("DEAD")

    active = await service.list_active()

    assert [c.code for c in active] == ["LIVE"]


async def test_update_changes_terms(connection: aiosqlite.Connection) -> None:
    service = _service(connection)
    await service.create(
        "EDIT", CouponKind.DISCOUNT, Decimal(5), max_uses=None, expires_at=None, created_by=1
    )

    updated = await service.update(
        "EDIT", discount_percent=Decimal("7.5"), max_uses=10, expires_at=None
    )

    assert updated.discount_percent == Decimal("7.5")
    assert updated.max_uses == 10


async def test_update_rejects_an_unknown_code(connection: aiosqlite.Connection) -> None:
    service = _service(connection)

    with pytest.raises(CouponNotFoundError):
        await service.update("GHOST", discount_percent=Decimal(5), max_uses=None, expires_at=None)


async def test_delete_removes_the_coupon(connection: aiosqlite.Connection) -> None:
    service = _service(connection)
    await service.create(
        "GONE", CouponKind.DISCOUNT, Decimal(5), max_uses=None, expires_at=None, created_by=1
    )

    await service.delete("GONE")

    with pytest.raises(CouponNotFoundError):
        await service.redeem(
            "GONE", channel_id=111, discord_id=222, ticket_kind=TicketKind.ORDER_BOOSTS
        )


async def test_delete_rejects_an_unknown_code(connection: aiosqlite.Connection) -> None:
    service = _service(connection)

    with pytest.raises(CouponNotFoundError):
        await service.delete("GHOST")
