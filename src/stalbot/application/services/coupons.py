"""`CouponService` — create, edit, and redeem coupons (заявка 26.08+27.08.2026)."""

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from stalbot.application.ports.clock import Clock
from stalbot.domain.entities.coupon import Coupon
from stalbot.domain.enums import CouponKind, TicketKind
from stalbot.domain.errors import (
    CouponAlreadyRedeemedError,
    CouponInactiveError,
    CouponNotFoundError,
    CouponWrongKindError,
)
from stalbot.infrastructure.cache.repositories.coupons import CouponsRepository

#: Which ticket kinds each coupon kind is allowed on (заявка 27.08.2026 п.10).
_ALLOWED_TICKET_KINDS: dict[CouponKind, frozenset[TicketKind]] = {
    CouponKind.DISCOUNT: frozenset({TicketKind.ORDER_BOOSTS}),
    CouponKind.MARKUP: frozenset({TicketKind.SELL_ITEMS, TicketKind.SELL_BOOSTS}),
}


class CouponService:
    """Validates/redeems a typed code, and backs `/coupons`' admin CRUD."""

    def __init__(self, coupons: CouponsRepository, *, clock: Clock) -> None:
        """Wire the service to its collaborator.

        Args:
            coupons: Cache repository for `coupons`/`coupon_redemptions`.
            clock: Time source, tz-aware `GMT3`, for `expires_at` comparisons.
        """
        self._coupons = coupons
        self._clock = clock

    async def create(
        self,
        code: str,
        kind: CouponKind,
        discount_percent: Decimal,
        *,
        max_uses: int | None,
        expires_at: datetime | None,
        created_by: int | None,
    ) -> Coupon:
        """Create a new active coupon (`/coupon_add`).

        Args:
            code: The code players will type.
            kind: Discount (заказ бустов) or markup (скупка).
            discount_percent: E.g. `Decimal("1.5")` for 1.5%.
            max_uses: Total redemption cap, or `None` for unlimited.
            expires_at: When the coupon stops working, or `None`.
            created_by: Discord id of the admin creating it.
        """
        return await self._coupons.create(
            code,
            kind,
            discount_percent,
            max_uses=max_uses,
            expires_at=expires_at,
            created_by=created_by,
            now=self._clock.now(),
        )

    async def list_active(self) -> Sequence[Coupon]:
        """Return every currently-active coupon (`/coupons`)."""
        return await self._coupons.all_active()

    async def get(self, code: str) -> Coupon | None:
        """Look up a coupon by code, for `/coupons`' edit flow.

        Args:
            code: The coupon's code.
        """
        return await self._coupons.get_by_code(code)

    async def update(
        self,
        code: str,
        *,
        discount_percent: Decimal,
        max_uses: int | None,
        expires_at: datetime | None,
    ) -> Coupon:
        """Change an existing coupon's terms (`/coupons`' edit button).

        Args:
            code: The coupon's code.
            discount_percent: New percent.
            max_uses: New cap, or `None` for unlimited.
            expires_at: New expiry, or `None` for none.

        Raises:
            CouponNotFoundError: No coupon with this code exists.
        """
        coupon = await self._coupons.get_by_code(code)
        if coupon is None:
            raise CouponNotFoundError(code)
        assert coupon.id is not None  # noqa: S101 - a fetched coupon always has an id
        await self._coupons.update(
            coupon.id,
            discount_percent=discount_percent,
            max_uses=max_uses,
            expires_at=expires_at,
        )
        updated = await self._coupons.get_by_id(coupon.id)
        assert updated is not None  # noqa: S101 - just updated
        return updated

    async def disable(self, code: str) -> Coupon:
        """Deactivate a coupon (`/coupon_disable`) — history/redemptions are kept.

        Args:
            code: The coupon's code.

        Raises:
            CouponNotFoundError: No coupon with this code exists.
        """
        coupon = await self._coupons.get_by_code(code)
        if coupon is None:
            raise CouponNotFoundError(code)
        assert coupon.id is not None  # noqa: S101 - a fetched coupon always has an id
        await self._coupons.set_active(coupon.id, False)
        return coupon

    async def delete(self, code: str) -> Coupon:
        """Permanently remove a coupon and its redemption history (`/coupon_delete`).

        Args:
            code: The coupon's code.

        Raises:
            CouponNotFoundError: No coupon with this code exists.
        """
        coupon = await self._coupons.get_by_code(code)
        if coupon is None:
            raise CouponNotFoundError(code)
        assert coupon.id is not None  # noqa: S101 - a fetched coupon always has an id
        await self._coupons.delete(coupon.id)
        return coupon

    async def redeem(
        self, code: str, *, channel_id: int, discord_id: int, ticket_kind: TicketKind
    ) -> Coupon:
        """Validate *code* and record its redemption by *discord_id*.

        Args:
            code: The typed code.
            channel_id: The ticket channel it's being applied in.
            discord_id: The redeeming account.
            ticket_kind: The ticket's kind — must match the coupon's `kind`
                (заявка 27.08.2026 п.10).

        Raises:
            CouponNotFoundError: No coupon with this code exists.
            CouponInactiveError: Disabled, expired, or its `max_uses` cap is hit.
            CouponWrongKindError: A discount coupon outside заказ бустов, or
                a markup one outside скупка.
            CouponAlreadyRedeemedError: This account already redeemed it once.
        """
        coupon = await self._coupons.get_by_code(code)
        if coupon is None:
            raise CouponNotFoundError(code)
        assert coupon.id is not None  # noqa: S101 - a fetched coupon always has an id

        now = self._clock.now()
        if not coupon.active:
            raise CouponInactiveError(code)
        if coupon.expires_at is not None and now >= coupon.expires_at:
            raise CouponInactiveError(code)
        if coupon.max_uses is not None and coupon.used_count >= coupon.max_uses:
            raise CouponInactiveError(code)
        if ticket_kind not in _ALLOWED_TICKET_KINDS[coupon.kind]:
            raise CouponWrongKindError(code)
        if await self._coupons.has_redeemed(coupon.id, discord_id):
            raise CouponAlreadyRedeemedError(code)

        redeemed = await self._coupons.redeem(
            coupon.id, channel_id=channel_id, discord_id=discord_id, now=now
        )
        if not redeemed:
            # Lost a race against a concurrent redemption by the same account
            # between the `has_redeemed` check above and this `INSERT`.
            raise CouponAlreadyRedeemedError(code)
        return coupon
