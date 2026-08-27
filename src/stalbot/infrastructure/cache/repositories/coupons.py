"""SQLite-backed `coupons`/`coupon_redemptions` (заявка 26.08+27.08.2026, migrations 0009-0010)."""

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

import aiosqlite

from stalbot.domain.entities.coupon import Coupon
from stalbot.domain.enums import CouponKind
from stalbot.infrastructure.cache.db import transaction


class CouponsRepository:
    """CRUD for coupons, plus the one-redemption-per-account ledger."""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        """Wrap an already-open cache connection.

        Args:
            connection: Connection returned by `CacheDb.connect()`.
        """
        self._conn = connection

    async def get_by_id(self, coupon_id: int) -> Coupon | None:
        """Look up a coupon by its surrogate id.

        Args:
            coupon_id: The coupon's id.
        """
        cursor = await self._conn.execute("SELECT * FROM coupons WHERE id = ?", (coupon_id,))
        row = await cursor.fetchone()
        return _row_to_coupon(row) if row is not None else None

    async def get_by_code(self, code: str) -> Coupon | None:
        """Look up a coupon by its code (case-insensitive — stored upper-cased).

        Args:
            code: The typed code, any case.
        """
        cursor = await self._conn.execute(
            "SELECT * FROM coupons WHERE code = ?", (code.strip().upper(),)
        )
        row = await cursor.fetchone()
        return _row_to_coupon(row) if row is not None else None

    async def all_active(self) -> Sequence[Coupon]:
        """Return every currently-active coupon, newest first (`/coupons`)."""
        cursor = await self._conn.execute(
            "SELECT * FROM coupons WHERE active = 1 ORDER BY id DESC"
        )
        return [_row_to_coupon(row) async for row in cursor]

    async def create(
        self,
        code: str,
        kind: CouponKind,
        discount_percent: Decimal,
        *,
        max_uses: int | None,
        expires_at: datetime | None,
        created_by: int | None,
        now: datetime,
    ) -> Coupon:
        """Insert a new active coupon.

        Args:
            code: The code players will type — normalized to upper-case.
            kind: `DISCOUNT` (заказ бустов) or `MARKUP` (скупка).
            discount_percent: E.g. `Decimal("1.5")` for 1.5%.
            max_uses: Total redemption cap across every player, or `None`.
            expires_at: When the coupon stops working, or `None`.
            created_by: Discord id of the admin who created it.
            now: Timestamp for `created_at`.
        """
        async with transaction(self._conn):
            await self._conn.execute(
                """
                INSERT INTO coupons
                    (code, kind, discount_percent, max_uses, used_count, active,
                     created_by, created_at, expires_at)
                VALUES (?, ?, ?, ?, 0, 1, ?, ?, ?)
                """,
                (
                    code.strip().upper(),
                    kind.value,
                    str(discount_percent),
                    max_uses,
                    created_by,
                    now.isoformat(),
                    expires_at.isoformat() if expires_at is not None else None,
                ),
            )
        coupon = await self.get_by_code(code)
        assert coupon is not None  # noqa: S101 - just inserted (or lost the race to one that did)
        return coupon

    async def update(
        self,
        coupon_id: int,
        *,
        discount_percent: Decimal,
        max_uses: int | None,
        expires_at: datetime | None,
    ) -> None:
        """Change an existing coupon's terms (`/coupons`' edit flow).

        Does not touch `code`, `kind`, `used_count`, or `active` — editing
        those is a different, more deliberate action (recreate, or
        `/coupon_disable`).

        Args:
            coupon_id: The coupon to update.
            discount_percent: New percent.
            max_uses: New cap, or `None` for unlimited.
            expires_at: New expiry, or `None` for none.
        """
        async with transaction(self._conn):
            await self._conn.execute(
                "UPDATE coupons SET discount_percent = ?, max_uses = ?, expires_at = ? WHERE id = ?",
                (
                    str(discount_percent),
                    max_uses,
                    expires_at.isoformat() if expires_at is not None else None,
                    coupon_id,
                ),
            )

    async def set_active(self, coupon_id: int, active: bool) -> None:
        """Enable or disable a coupon without deleting its history.

        Args:
            coupon_id: The coupon to update.
            active: New state.
        """
        async with transaction(self._conn):
            await self._conn.execute(
                "UPDATE coupons SET active = ? WHERE id = ?", (int(active), coupon_id)
            )

    async def delete(self, coupon_id: int) -> None:
        """Permanently remove a coupon and its redemption history.

        Args:
            coupon_id: The coupon to delete. `coupon_redemptions` cascades.
        """
        async with transaction(self._conn):
            await self._conn.execute("DELETE FROM coupons WHERE id = ?", (coupon_id,))

    async def has_redeemed(self, coupon_id: int, discord_id: int) -> bool:
        """Whether *discord_id* has already redeemed this coupon.

        Args:
            coupon_id: The coupon to check.
            discord_id: The Discord account to check.
        """
        cursor = await self._conn.execute(
            "SELECT 1 FROM coupon_redemptions WHERE coupon_id = ? AND discord_id = ?",
            (coupon_id, discord_id),
        )
        return await cursor.fetchone() is not None

    async def redeem(
        self, coupon_id: int, *, channel_id: int, discord_id: int, now: datetime
    ) -> bool:
        """Record a redemption and bump `used_count`, atomically.

        The `UNIQUE(coupon_id, discord_id)` constraint is the actual
        concurrency guard — two simultaneous redemption attempts by the
        same account race on this `INSERT`, and only one wins.

        Args:
            coupon_id: The coupon being redeemed.
            channel_id: The ticket channel it was redeemed in.
            discord_id: The redeeming account.
            now: Timestamp for `redeemed_at`.

        Returns:
            `True` if this call recorded the redemption, `False` if
            *discord_id* had already redeemed this coupon (no-op).
        """
        async with transaction(self._conn):
            try:
                await self._conn.execute(
                    """
                    INSERT INTO coupon_redemptions (coupon_id, channel_id, discord_id, redeemed_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (coupon_id, channel_id, discord_id, now.isoformat()),
                )
            except aiosqlite.IntegrityError:
                return False
            await self._conn.execute(
                "UPDATE coupons SET used_count = used_count + 1 WHERE id = ?", (coupon_id,)
            )
        return True


def _row_to_coupon(row: aiosqlite.Row) -> Coupon:
    return Coupon(
        id=row["id"],
        code=row["code"],
        kind=CouponKind(row["kind"]),
        discount_percent=Decimal(row["discount_percent"]),
        max_uses=row["max_uses"],
        used_count=row["used_count"],
        active=bool(row["active"]),
        created_by=row["created_by"],
        created_at=datetime.fromisoformat(row["created_at"]),
        expires_at=datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None,
    )
