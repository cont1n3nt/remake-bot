"""Tests for `stalbot.presentation.cogs.pricing.PricingCog` (PLAN.md §10.6-§10.8).

`PricingService`/`CatalogItemsRepository` are mocked — their own behavior is
covered in `tests/unit/application/services/test_pricing.py`. This file is
about whether the cog validates input, confirms imports, and reports right.

sqlite_migration.md Э7: `/sync_prices` is gone along with the price sheets
it used to push onto.
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import app_commands

from stalbot.application.dto.price_change import PriceChange
from stalbot.application.dto.price_import import PriceImportIssue, PriceImportPlan
from stalbot.domain.entities.catalog_item import CatalogItem
from stalbot.domain.entities.temp_price import TempPrice
from stalbot.domain.enums import ItemCategory, PriceField
from stalbot.domain.money import Rub
from stalbot.presentation.cogs.pricing import PricingCog
from stalbot.presentation.embeds.factory import EmbedFactory

_NOW = datetime(2026, 7, 31, 21, 45, tzinfo=UTC)


def _item(**overrides: object) -> CatalogItem:
    defaults: dict[str, object] = {
        "id": 1,
        "name": "Хвост тушкана",
        "name_norm": "хвост тушкана",
        "category": ItemCategory.RESOURCE,
        "section": None,
        "price_buy": Rub(18000),
        "price_sell": None,
        "emoji": None,
        "sort_order": 0,
        "shelter_item_id": None,
        "created_at": _NOW,
        "updated_at": None,
        "deleted_at": None,
    }
    defaults.update(overrides)
    return CatalogItem(**defaults)  # type: ignore[arg-type]


def _change(**overrides: object) -> PriceChange:
    defaults: dict[str, object] = {
        "item_id": 1,
        "item_name": "Хвост тушкана",
        "category": ItemCategory.RESOURCE,
        "field": PriceField.BUY,
        "old_price": Decimal(18000),
        "new_price": Decimal(19500),
    }
    defaults.update(overrides)
    return PriceChange(**defaults)  # type: ignore[arg-type]


def _cog(
    *,
    set_price_result: PriceChange | None = None,
    all_items: list[CatalogItem] | None = None,
    by_category: dict[ItemCategory, list[CatalogItem]] | None = None,
    settings: MagicMock | None = None,
    temp_prices: MagicMock | None = None,
) -> tuple[PricingCog, MagicMock, MagicMock]:
    pricing = MagicMock()
    pricing.set_price = AsyncMock(return_value=set_price_result or _change())
    pricing.preview_import = AsyncMock(return_value=PriceImportPlan())
    pricing.apply_import = AsyncMock()
    items = MagicMock()
    items.all = AsyncMock(return_value=all_items or [_item()])
    items.get_by_id = AsyncMock(return_value=next(iter(all_items or [_item()]), None))
    by_category = by_category or {}
    items.by_category = AsyncMock(side_effect=lambda category: by_category.get(category, []))
    settings = settings or MagicMock(price_import_confirm=True)
    temp_prices = temp_prices or _fake_temp_prices()
    cog = PricingCog(pricing, items, EmbedFactory(), settings, temp_prices)
    return cog, pricing, items


def _fake_temp_prices(
    *, result: PriceChange | None = None, active: list[TempPrice] | None = None
) -> MagicMock:
    temp_prices = MagicMock()
    temp_prices.set_temp_price = AsyncMock(return_value=result or _change())
    temp_prices.list_active = AsyncMock(return_value=active or [])
    return temp_prices


def _temp_price(**overrides: object) -> TempPrice:
    defaults: dict[str, object] = {
        "id": 1,
        "item_id": 1,
        "field": PriceField.BUY,
        "original_price": Rub(18000),
        "expires_at": _NOW,
        "created_by": 42,
        "created_at": _NOW,
    }
    defaults.update(overrides)
    return TempPrice(**defaults)  # type: ignore[arg-type]


def _interaction(*, user_id: int = 1) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock(return_value=MagicMock(spec=discord.Message))
    interaction.user = MagicMock(id=user_id)
    return interaction


def _attachment(
    *, filename: str = "prices.txt", size: int = 100, content: bytes = b""
) -> MagicMock:
    attachment = MagicMock(spec=discord.Attachment)
    attachment.filename = filename
    attachment.size = size
    attachment.read = AsyncMock(return_value=content)
    return attachment


# --- setprice / setboost ---------------------------------------------------


async def test_setprice_sets_buy_field_and_reports() -> None:
    cog, pricing, _items = _cog(set_price_result=_change())
    interaction = _interaction(user_id=42)

    callback: Any = PricingCog.setprice.callback
    await callback(cog, interaction, 1, "19500")

    pricing.set_price.assert_awaited_once_with(1, PriceField.BUY, Decimal(19500), changed_by=42)
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "Хвост тушкана" in (embed.description or "")


async def test_setboost_sets_sell_field_and_reports() -> None:
    change = _change(field=PriceField.SELL, category=ItemCategory.BOOST, item_name="Топот")
    cog, pricing, _items = _cog(set_price_result=change)
    interaction = _interaction(user_id=42)

    callback: Any = PricingCog.setboost.callback
    await callback(cog, interaction, 2, "300000")

    pricing.set_price.assert_awaited_once_with(2, PriceField.SELL, Decimal(300000), changed_by=42)
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "Топот" in (embed.description or "")


async def test_setprice_autocomplete_scopes_to_resources() -> None:
    resources = [_item(id=1, name="Хвост")]
    cog, _pricing, _items = _cog(by_category={ItemCategory.RESOURCE: resources})
    interaction = _interaction()

    autocomplete: Any = getattr(cog, "_setprice_autocomplete")  # noqa: B009
    choices = await autocomplete(interaction, "хво")

    assert [c.value for c in choices] == [1]


async def test_setboost_autocomplete_scopes_to_boosts() -> None:
    boosts = [_item(id=2, name="Топот", category=ItemCategory.BOOST)]
    cog, _pricing, _items = _cog(by_category={ItemCategory.BOOST: boosts})
    interaction = _interaction()

    autocomplete: Any = getattr(cog, "_setboost_autocomplete")  # noqa: B009
    choices = await autocomplete(interaction, "топ")

    assert [c.value for c in choices] == [2]


# --- temp_price ------------------------------------------------------------


async def test_temp_price_applies_the_override_and_reports() -> None:
    item = _item(id=1, category=ItemCategory.RESOURCE)
    change = _change(field=PriceField.BUY)
    temp_prices = _fake_temp_prices(result=change)
    cog, _pricing, _items = _cog(all_items=[item], temp_prices=temp_prices)
    interaction = _interaction(user_id=42)

    callback: Any = PricingCog.temp_price.callback
    await callback(cog, interaction, 1, "19500", "через 3 часа")

    temp_prices.set_temp_price.assert_awaited_once()
    args, kwargs = temp_prices.set_temp_price.call_args
    assert args[0] == 1
    assert args[1] is PriceField.BUY
    assert args[2] == Decimal(19500)
    assert kwargs["changed_by"] == 42
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "Хвост тушкана" in (embed.description or "")


async def test_temp_price_picks_the_sell_field_for_a_boost() -> None:
    item = _item(id=2, category=ItemCategory.BOOST, price_buy=None, price_sell=Rub(300000))
    temp_prices = _fake_temp_prices()
    cog, _pricing, _items = _cog(all_items=[item], temp_prices=temp_prices)
    interaction = _interaction(user_id=42)

    callback: Any = PricingCog.temp_price.callback
    await callback(cog, interaction, 2, "350000", "через 3 часа")

    args, _kwargs = temp_prices.set_temp_price.call_args
    assert args[1] is PriceField.SELL


async def test_temp_price_rejects_an_unparsable_deadline() -> None:
    cog, _pricing, _items = _cog()
    interaction = _interaction()

    callback: Any = PricingCog.temp_price.callback
    await callback(cog, interaction, 1, "19500", "not a date")

    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "дату" in (embed.description or "")


async def test_temp_price_rejects_an_unknown_item() -> None:
    items_mock = MagicMock()
    items_mock.get_by_id = AsyncMock(return_value=None)
    items_mock.all = AsyncMock(return_value=[])
    cog = PricingCog(
        MagicMock(), items_mock, EmbedFactory(), MagicMock(), _fake_temp_prices()
    )
    interaction = _interaction()

    callback: Any = PricingCog.temp_price.callback
    await callback(cog, interaction, 999, "19500", "через 3 часа")

    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "не найден" in (embed.description or "")


# --- temp_prices (list) ----------------------------------------------------


async def test_temp_prices_reports_when_nothing_is_active() -> None:
    cog, _pricing, _items = _cog(temp_prices=_fake_temp_prices(active=[]))
    interaction = _interaction()

    callback: Any = PricingCog.temp_prices.callback
    await callback(cog, interaction)

    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "нет активных" in (embed.description or "")


async def test_temp_prices_lists_active_overrides() -> None:
    item = _item(id=1, name="Хвост тушкана", price_buy=Rub(19500))
    active = [_temp_price(item_id=1, field=PriceField.BUY, original_price=Rub(18000))]
    cog, _pricing, _items = _cog(all_items=[item], temp_prices=_fake_temp_prices(active=active))
    interaction = _interaction()

    callback: Any = PricingCog.temp_prices.callback
    await callback(cog, interaction)

    embed = interaction.followup.send.call_args.kwargs["embed"]
    description = embed.description or ""
    assert "Хвост тушкана" in description
    assert "18" in description


# --- new_price -----------------------------------------------------------


async def test_new_price_rejects_non_txt_attachment() -> None:
    cog, pricing, _items = _cog()
    interaction = _interaction()

    callback: Any = PricingCog.new_price.callback
    await callback(cog, interaction, _attachment(filename="prices.csv"))

    pricing.preview_import.assert_not_called()
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert ".txt" in (embed.description or "")


async def test_new_price_rejects_oversized_attachment() -> None:
    cog, pricing, _items = _cog()
    interaction = _interaction()

    callback: Any = PricingCog.new_price.callback
    await callback(cog, interaction, _attachment(size=2_000_000))

    pricing.preview_import.assert_not_called()
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "1 МБ" in (embed.description or "")


async def test_new_price_reports_validation_issues_without_applying() -> None:
    cog, pricing, _items = _cog()
    pricing.preview_import = AsyncMock(
        return_value=PriceImportPlan(issues=(PriceImportIssue(3, "некорректная цена"),))
    )
    interaction = _interaction()

    callback: Any = PricingCog.new_price.callback
    await callback(cog, interaction, _attachment(content=b"bad"))

    pricing.apply_import.assert_not_called()
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "Строка 3" in (embed.description or "")


async def test_new_price_reports_no_changes_without_applying() -> None:
    cog, pricing, _items = _cog()
    pricing.preview_import = AsyncMock(return_value=PriceImportPlan())
    interaction = _interaction()

    content = "1 | Хвост | resource | 18000 |  |  | \n".encode()
    callback: Any = PricingCog.new_price.callback
    await callback(cog, interaction, _attachment(content=content))

    pricing.apply_import.assert_not_called()
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "Изменений нет" in (embed.title or "")


async def test_new_price_applies_immediately_when_confirm_disabled() -> None:
    settings = MagicMock(price_import_confirm=False)
    cog, pricing, _items = _cog(settings=settings)
    pricing.preview_import = AsyncMock(return_value=PriceImportPlan(changes=(_change(),)))
    interaction = _interaction(user_id=42)

    callback: Any = PricingCog.new_price.callback
    await callback(cog, interaction, _attachment())

    pricing.apply_import.assert_awaited_once()
    assert pricing.apply_import.call_args.kwargs.get("changed_by") == 42
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "Цены обновлены" in (embed.title or "")


async def test_new_price_waits_for_confirmation_before_applying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cog, pricing, _items = _cog()
    pricing.preview_import = AsyncMock(return_value=PriceImportPlan(changes=(_change(),)))
    interaction = _interaction()

    fake_view = MagicMock()
    fake_view.wait = AsyncMock()
    fake_view.confirmed = None
    monkeypatch.setattr(
        "stalbot.presentation.cogs.pricing.ConfirmView", MagicMock(return_value=fake_view)
    )

    callback: Any = PricingCog.new_price.callback
    await callback(cog, interaction, _attachment())

    pricing.apply_import.assert_not_called()
    kwargs = interaction.followup.send.call_args_list[0].kwargs
    assert kwargs["embed"].title == "✏️ Подтвердите изменение цен"
    assert "view" in kwargs


# -- SEC-5: cooldown on the heavy admin command ------------------------


def _admin_interaction(user_id: int = 1) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock(spec=discord.Member, id=user_id)
    interaction.user.guild_permissions = MagicMock(administrator=True)
    interaction.created_at = datetime(2026, 8, 5, 12, 0, 0, tzinfo=UTC)
    return interaction


async def test_a_non_admins_call_does_not_consume_the_cooldown_bucket() -> None:
    """`admin_only()`'s check must run before the cooldown check in the accumulated
    `checks` list — decorator order in source has `@app_commands.checks.cooldown(...)`
    above `@admin_only()`, but `app_commands.check` appends bottom-up, so the
    textually-inner `admin_only()` check ends up evaluated first. If that ordering
    were ever reversed, a non-admin's rejected attempt would still consume the
    cooldown token for their user id — this proves it doesn't, by reusing the exact
    same identity and moment for a follow-up admin call that must still succeed.
    """
    command = PricingCog.new_price
    user_id = 4002

    non_admin = _admin_interaction(user_id)
    non_admin.user.guild_permissions = MagicMock(administrator=False)
    assert await command._check_can_run(non_admin) is False

    admin = _admin_interaction(user_id)  # same identity, same `created_at` moment
    assert await command._check_can_run(admin) is True


async def test_heavy_command_rejects_a_second_call_within_the_cooldown_window() -> None:
    """A second invocation at the same moment (same `interaction.created_at`) must be
    throttled — `app_commands.checks.cooldown` keys entirely off that timestamp, not
    real wall-clock time, so this is deterministic without any sleeping/mocking.
    """
    command = PricingCog.new_price
    interaction = _admin_interaction(1002)

    for check in command.checks:
        assert await discord.utils.maybe_coroutine(check, interaction) is True

    with pytest.raises(app_commands.CommandOnCooldown):
        for check in command.checks:
            await discord.utils.maybe_coroutine(check, interaction)


async def test_heavy_command_allows_a_second_call_after_the_window() -> None:
    command = PricingCog.new_price
    user_id = 2002
    first = _admin_interaction(user_id)
    for check in command.checks:
        assert await discord.utils.maybe_coroutine(check, first) is True

    later = _admin_interaction(user_id)
    later.created_at = datetime(2026, 8, 5, 12, 1, 0, tzinfo=UTC)  # +60s, well past the 15s window
    for check in command.checks:
        assert await discord.utils.maybe_coroutine(check, later) is True
