"""`Coupon` — a row of the `coupons` table (заявка 26.08.2026)."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from stalbot.domain.enums import CouponKind


@dataclass(frozen=True, slots=True)
class Coupon:
    """One percent-off/on code, redeemable at most once per Discord account."""

    id: int | None
    """`None` for a not-yet-persisted coupon — the repository assigns one on insert."""
    code: str
    """Stored upper-cased — lookups normalize the typed code the same way."""
    kind: CouponKind
    """`DISCOUNT` (заказ бустов only) or `MARKUP` (скупка/скуп only)."""
    discount_percent: Decimal
    """E.g. `Decimal("1.5")` for 1.5% off."""
    max_uses: int | None
    """`None` = unlimited."""
    used_count: int
    active: bool
    created_by: int | None
    created_at: datetime
    expires_at: datetime | None
