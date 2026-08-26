"""Tests for `stalbot.presentation.cogs.coupons.CouponsCog` (заявка 26.08.2026)."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from stalbot.domain.entities.coupon import Coupon
from stalbot.domain.errors import CouponNotFoundError
from stalbot.presentation.cogs.coupons import CouponsCog
from stalbot.presentation.embeds.factory import EmbedFactory

_NOW = datetime(2026, 8, 26, tzinfo=UTC)


def _coupon(**overrides: object) -> Coupon:
    defaults: dict[str, object] = {
        "id": 1,
        "code": "KLONDIKE10",
        "discount_percent": Decimal("1.5"),
        "max_uses": None,
        "used_count": 0,
        "active": True,
        "created_by": 1,
        "created_at": _NOW,
        "expires_at": None,
    }
    defaults.update(overrides)
    return Coupon(**defaults)  # type: ignore[arg-type]


def _cog(*, coupons: MagicMock | None = None) -> tuple[CouponsCog, MagicMock]:
    if coupons is None:
        coupons = MagicMock()
        coupons.create = AsyncMock(return_value=_coupon())
        coupons.disable = AsyncMock(return_value=_coupon())
    cog = CouponsCog(coupons, EmbedFactory())
    return cog, coupons


def _interaction() -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock(id=42)
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock(return_value=MagicMock(spec=discord.Message))
    return interaction


async def test_coupon_add_creates_and_reports() -> None:
    service = MagicMock()
    service.create = AsyncMock(
        return_value=_coupon(code="KLONDIKE10", discount_percent=Decimal("1.5"))
    )
    cog, _service = _cog(coupons=service)
    interaction = _interaction()

    callback: Any = CouponsCog.coupon_add.callback
    await callback(cog, interaction, "klondike10", "1.5", None, None)

    service.create.assert_awaited_once_with(
        "klondike10", Decimal("1.5"), max_uses=None, expires_at=None, created_by=42
    )
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "KLONDIKE10" in (embed.description or "")


async def test_coupon_add_rejects_a_non_numeric_discount() -> None:
    cog, service = _cog()
    interaction = _interaction()

    callback: Any = CouponsCog.coupon_add.callback
    await callback(cog, interaction, "CODE", "notanumber", None, None)

    service.create.assert_not_called()
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "числом" in (embed.description or "")


async def test_coupon_add_rejects_a_discount_out_of_range() -> None:
    cog, service = _cog()
    interaction = _interaction()

    callback: Any = CouponsCog.coupon_add.callback
    await callback(cog, interaction, "CODE", "150", None, None)

    service.create.assert_not_called()
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "от 0 до 100" in (embed.description or "")


async def test_coupon_add_rejects_an_unparsable_deadline() -> None:
    cog, service = _cog()
    interaction = _interaction()

    callback: Any = CouponsCog.coupon_add.callback
    await callback(cog, interaction, "CODE", "5", None, "not a date")

    service.create.assert_not_called()
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "дату" in (embed.description or "")


async def test_coupon_disable_reports_success() -> None:
    cog, service = _cog()
    interaction = _interaction()

    callback: Any = CouponsCog.coupon_disable.callback
    await callback(cog, interaction, "KLONDIKE10")

    service.disable.assert_awaited_once_with("KLONDIKE10")
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "KLONDIKE10" in (embed.description or "")


async def test_coupon_disable_reports_an_unknown_code() -> None:
    service = MagicMock()
    service.disable = AsyncMock(side_effect=CouponNotFoundError("GHOST"))
    cog, _service = _cog(coupons=service)
    interaction = _interaction()

    callback: Any = CouponsCog.coupon_disable.callback
    await callback(cog, interaction, "GHOST")

    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "не найден" in (embed.description or "")
