"""Tests for `stalbot.presentation.cogs.coupons.CouponsCog` (заявка 26.08+27.08.2026)."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from stalbot.domain.entities.coupon import Coupon
from stalbot.domain.enums import CouponKind
from stalbot.domain.errors import CouponNotFoundError
from stalbot.presentation.cogs.coupons import CouponsCog, _CouponsView
from stalbot.presentation.embeds.factory import EmbedFactory

_NOW = datetime(2026, 8, 26, tzinfo=UTC)


def _coupon(**overrides: object) -> Coupon:
    defaults: dict[str, object] = {
        "id": 1,
        "code": "KLONDIKE10",
        "kind": CouponKind.DISCOUNT,
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
        coupons.get = AsyncMock(return_value=_coupon())
        coupons.list_active = AsyncMock(return_value=[])
        coupons.update = AsyncMock(return_value=_coupon())
        coupons.delete = AsyncMock(return_value=_coupon())
    cog = CouponsCog(coupons, EmbedFactory())
    return cog, coupons


def _interaction() -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock(id=42)
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock(return_value=MagicMock(spec=discord.Message))
    interaction.original_response = AsyncMock(return_value=MagicMock(spec=discord.Message))
    return interaction


def _discount_choice() -> Any:
    from discord import app_commands

    return app_commands.Choice(name="discount", value=CouponKind.DISCOUNT.value)


# --- coupon_add --------------------------------------------------------


async def test_coupon_add_creates_and_reports() -> None:
    service = MagicMock()
    service.create = AsyncMock(
        return_value=_coupon(code="KLONDIKE10", discount_percent=Decimal("1.5"))
    )
    cog, _service = _cog(coupons=service)
    interaction = _interaction()

    callback: Any = CouponsCog.coupon_add.callback
    await callback(cog, interaction, _discount_choice(), "klondike10", "1.5", None, None)

    service.create.assert_awaited_once_with(
        "klondike10",
        CouponKind.DISCOUNT,
        Decimal("1.5"),
        max_uses=None,
        expires_at=None,
        created_by=42,
    )
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "KLONDIKE10" in (embed.description or "")


async def test_coupon_add_rejects_a_non_numeric_percent() -> None:
    cog, service = _cog()
    interaction = _interaction()

    callback: Any = CouponsCog.coupon_add.callback
    await callback(cog, interaction, _discount_choice(), "CODE", "notanumber", None, None)

    service.create.assert_not_called()
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "числом" in (embed.description or "")


async def test_coupon_add_rejects_a_percent_out_of_range() -> None:
    cog, service = _cog()
    interaction = _interaction()

    callback: Any = CouponsCog.coupon_add.callback
    await callback(cog, interaction, _discount_choice(), "CODE", "150", None, None)

    service.create.assert_not_called()
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "от 0 до 100" in (embed.description or "")


async def test_coupon_add_rejects_an_unparsable_deadline() -> None:
    cog, service = _cog()
    interaction = _interaction()

    callback: Any = CouponsCog.coupon_add.callback
    await callback(cog, interaction, _discount_choice(), "CODE", "5", None, "not a date")

    service.create.assert_not_called()
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "дату" in (embed.description or "")


# --- coupon_disable / coupon_delete -------------------------------------


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


async def test_coupon_delete_confirms_then_deletes(monkeypatch: Any) -> None:
    fake_view = MagicMock()
    fake_view.wait = AsyncMock()
    fake_view.confirmed = True
    monkeypatch.setattr(
        "stalbot.presentation.cogs.coupons.ConfirmView", MagicMock(return_value=fake_view)
    )
    cog, service = _cog()
    interaction = _interaction()

    callback: Any = CouponsCog.coupon_delete.callback
    await callback(cog, interaction, "KLONDIKE10")

    service.delete.assert_awaited_once_with("KLONDIKE10")
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "удалён" in (embed.title or "")


async def test_coupon_delete_cancelled_does_not_delete(monkeypatch: Any) -> None:
    fake_view = MagicMock()
    fake_view.wait = AsyncMock()
    fake_view.confirmed = False
    monkeypatch.setattr(
        "stalbot.presentation.cogs.coupons.ConfirmView", MagicMock(return_value=fake_view)
    )
    cog, service = _cog()
    interaction = _interaction()

    callback: Any = CouponsCog.coupon_delete.callback
    await callback(cog, interaction, "KLONDIKE10")

    service.delete.assert_not_called()


async def test_coupon_delete_reports_an_unknown_code() -> None:
    service = MagicMock()
    service.get = AsyncMock(return_value=None)
    cog, _service = _cog(coupons=service)
    interaction = _interaction()

    callback: Any = CouponsCog.coupon_delete.callback
    await callback(cog, interaction, "GHOST")

    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "не найден" in (embed.description or "")


# --- coupons (list) ------------------------------------------------------


async def test_coupons_lists_active_coupons() -> None:
    service = MagicMock()
    service.list_active = AsyncMock(return_value=[_coupon(code="A"), _coupon(code="B", id=2)])
    cog, _service = _cog(coupons=service)
    interaction = _interaction()

    callback: Any = CouponsCog.coupons.callback
    await callback(cog, interaction)

    kwargs = interaction.followup.send.call_args.kwargs
    assert isinstance(kwargs["view"], _CouponsView)
    field_names = {field.name for field in kwargs["embed"].fields}
    assert field_names == {"A", "B"}


async def test_edit_submitted_updates_the_coupon() -> None:
    service = MagicMock()
    service.update = AsyncMock(return_value=_coupon(discount_percent=Decimal("7.5")))
    cog, _service = _cog(coupons=service)
    interaction = _interaction()

    await cog._on_edit_submitted(interaction, "KLONDIKE10", "7.5", "10", None)

    service.update.assert_awaited_once_with(
        "KLONDIKE10", discount_percent=Decimal("7.5"), max_uses=10, expires_at=None
    )
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "обновлён" in (embed.title or "")


async def test_edit_submitted_rejects_a_non_integer_max_uses() -> None:
    cog, service = _cog()
    interaction = _interaction()

    await cog._on_edit_submitted(interaction, "KLONDIKE10", "5", "not a number", None)

    service.update.assert_not_called()
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "целым числом" in (embed.description or "")
