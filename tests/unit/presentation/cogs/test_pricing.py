"""Tests for `stalbot.presentation.cogs.pricing.PricingCog` (PLAN.md §10.6-§10.8).

`PricingService`/`ItemsCacheRepository` are mocked — their own behavior is
covered in `tests/unit/application/services/test_pricing.py`. This file is
about whether the cog validates input, confirms imports, and reports right.
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
from stalbot.application.dto.sync_prices_report import SyncPricesReport
from stalbot.domain.entities.item import Item
from stalbot.domain.enums import ItemCategory, PriceField
from stalbot.presentation.cogs.pricing import PricingCog
from stalbot.presentation.embeds.factory import EmbedFactory


def _item(**overrides: object) -> Item:
    defaults: dict[str, object] = {
        "id": 1,
        "name": "Хвост тушкана",
        "category": ItemCategory.RESOURCE,
        "price_buy": Decimal(18000),
        "price_sell": None,
        "emoji": None,
        "updated_at": None,
        "row": 3,
    }
    defaults.update(overrides)
    return Item(**defaults)  # type: ignore[arg-type]


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
    sync_report: SyncPricesReport | None = None,
    all_items: list[Item] | None = None,
    by_category: dict[ItemCategory, list[Item]] | None = None,
    settings: MagicMock | None = None,
) -> tuple[PricingCog, MagicMock, MagicMock]:
    pricing = MagicMock()
    pricing.set_price = AsyncMock(return_value=set_price_result or _change())
    pricing.sync_prices = AsyncMock(return_value=sync_report or SyncPricesReport())
    pricing.preview_import = AsyncMock(return_value=PriceImportPlan())
    pricing.apply_import = AsyncMock()
    items = MagicMock()
    items.all = AsyncMock(return_value=all_items or [_item()])
    by_category = by_category or {}
    items.by_category = AsyncMock(side_effect=lambda category: by_category.get(category, []))
    settings = settings or MagicMock(price_import_confirm=True)
    cog = PricingCog(pricing, items, EmbedFactory(), settings)
    return cog, pricing, items


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
    interaction = _interaction()

    callback: Any = PricingCog.setprice.callback
    await callback(cog, interaction, 1, "19500")

    pricing.set_price.assert_awaited_once_with(1, PriceField.BUY, Decimal(19500))
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "Хвост тушкана" in (embed.description or "")


async def test_setboost_sets_sell_field_and_reports() -> None:
    change = _change(field=PriceField.SELL, category=ItemCategory.BOOST, item_name="Топот")
    cog, pricing, _items = _cog(set_price_result=change)
    interaction = _interaction()

    callback: Any = PricingCog.setboost.callback
    await callback(cog, interaction, 2, "300000")

    pricing.set_price.assert_awaited_once_with(2, PriceField.SELL, Decimal(300000))
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


# --- sync_prices -------------------------------------------------------


async def test_sync_prices_reports_updated_and_unchanged_counts() -> None:
    report = SyncPricesReport(updated=(_change(),), not_found=(), unchanged_count=3)
    cog, pricing, _items = _cog(sync_report=report)
    interaction = _interaction()

    callback: Any = PricingCog.sync_prices.callback
    await callback(cog, interaction)

    pricing.sync_prices.assert_awaited_once()
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "Обновлено: 1" in (embed.description or "")
    assert "Без изменений: 3" in (embed.description or "")


async def test_sync_prices_reports_not_found_names_with_overflow_count() -> None:
    not_found = tuple(f"Предмет {i}" for i in range(12))
    report = SyncPricesReport(not_found=not_found)
    cog, _pricing, _items = _cog(sync_report=report)
    interaction = _interaction()

    callback: Any = PricingCog.sync_prices.callback
    await callback(cog, interaction)

    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "и ещё 2" in (embed.description or "")


async def test_sync_prices_reports_unparseable_cells() -> None:
    """APP-3: surfaced separately from "not found" — this is an existing item
    whose price cell couldn't be read, not an unknown item."""
    report = SyncPricesReport(unparseable=("Топот",))
    cog, _pricing, _items = _cog(sync_report=report)
    interaction = _interaction()

    callback: Any = PricingCog.sync_prices.callback
    await callback(cog, interaction)

    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "Топот" in (embed.description or "")
    assert "не изменено" in (embed.description or "")


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
    interaction = _interaction()

    callback: Any = PricingCog.new_price.callback
    await callback(cog, interaction, _attachment())

    pricing.apply_import.assert_awaited_once()
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


# -- SEC-5: cooldown on the two heavy admin commands ------------------------


def _admin_interaction(user_id: int = 1) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock(spec=discord.Member, id=user_id)
    interaction.user.guild_permissions = MagicMock(administrator=True)
    interaction.created_at = datetime(2026, 8, 5, 12, 0, 0, tzinfo=UTC)
    return interaction


@pytest.mark.parametrize(
    ("command", "user_id"), [(PricingCog.sync_prices, 1001), (PricingCog.new_price, 1002)]
)
async def test_heavy_command_rejects_a_second_call_within_the_cooldown_window(
    command: app_commands.Command[PricingCog, ..., None], user_id: int
) -> None:
    """A second invocation at the same moment (same `interaction.created_at`) must be
    throttled — `app_commands.checks.cooldown` keys entirely off that timestamp, not
    real wall-clock time, so this is deterministic without any sleeping/mocking.

    Each command/test pair gets its own `user_id` (deliberately distinct from every
    other cooldown test in this module) — the cooldown mapping is a closure captured
    once when the decorator runs at class-definition time, so it persists across
    tests in the same process; reusing a `user_id` would leak state between tests.
    """
    interaction = _admin_interaction(user_id)

    for check in command.checks:
        assert await check(interaction) is True

    with pytest.raises(app_commands.CommandOnCooldown):
        for check in command.checks:
            await check(interaction)


@pytest.mark.parametrize(
    ("command", "user_id"), [(PricingCog.sync_prices, 2001), (PricingCog.new_price, 2002)]
)
async def test_heavy_command_allows_a_second_call_after_the_window(
    command: app_commands.Command[PricingCog, ..., None], user_id: int
) -> None:
    first = _admin_interaction(user_id)
    for check in command.checks:
        assert await check(first) is True

    later = _admin_interaction(user_id)
    later.created_at = datetime(2026, 8, 5, 12, 1, 0, tzinfo=UTC)  # +60s, well past the 15s window
    for check in command.checks:
        assert await check(later) is True
