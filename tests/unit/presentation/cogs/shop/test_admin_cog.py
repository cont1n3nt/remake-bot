"""Tests for `stalbot.presentation.cogs.shop.admin_cog.ShopAdminCog` (заявка 13.09.2026 п.2)."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import app_commands

from stalbot.application.services.shop import RefundResult
from stalbot.domain.entities.player import Player
from stalbot.domain.entities.shop import ShopCategory, ShopItem, ShopPurchase
from stalbot.domain.shop.effects import EffectKind
from stalbot.presentation.cogs.shop.admin_cog import ShopAdminCog
from stalbot.presentation.embeds.factory import EmbedFactory

_NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)


def _category(key: str = "small") -> ShopCategory:
    return ShopCategory(key=key, name="🟢 Мелкие товары", description="Недорогие мелочи")


def _item(item_id: int = 1, **overrides: object) -> ShopItem:
    defaults: dict[str, object] = {
        "id": item_id,
        "category_key": "small",
        "name": "Кофе",
        "name_norm": "кофе",
        "description": "Бодрит",
        "price_coins": 50,
        "effect_kind": "manual",
    }
    defaults.update(overrides)
    return ShopItem(**defaults)  # type: ignore[arg-type]


def _purchase(purchase_id: int = 1, **overrides: object) -> ShopPurchase:
    defaults: dict[str, object] = {
        "id": purchase_id,
        "player_id": 1,
        "shop_item_id": 1,
        "price_coins": 50,
    }
    defaults.update(overrides)
    return ShopPurchase(**defaults)  # type: ignore[arg-type]


def _player(**overrides: object) -> Player:
    defaults: dict[str, object] = {
        "id": 1,
        "nick_norm": "scaryyyyy",
        "nick_display": "Scaryyyyy",
        "discord_id": 42,
        "referrer_player_id": None,
        "is_booster": False,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    defaults.update(overrides)
    return Player(**defaults)  # type: ignore[arg-type]


def _effect_choice(kind: EffectKind = EffectKind.MANUAL) -> app_commands.Choice[str]:
    return app_commands.Choice(name=kind.value, value=kind.value)


def _shop(**overrides: object) -> MagicMock:
    shop = MagicMock()
    shop.upsert_category = AsyncMock(return_value=overrides.get("category", _category()))
    shop.categories = AsyncMock(return_value=overrides.get("categories", [_category()]))
    shop.add_item = AsyncMock(return_value=overrides.get("added_item", _item()))
    shop.get_item = AsyncMock(return_value=overrides.get("get_item_return", _item()))
    shop.update_item = AsyncMock(return_value=overrides.get("updated_item", _item()))
    shop.delete_item = AsyncMock(return_value=overrides.get("deleted_item", _item()))
    shop.items = AsyncMock(return_value=overrides.get("items", [_item()]))
    shop.recent_purchases = AsyncMock(return_value=overrides.get("recent", [_purchase()]))
    return shop


def _players(**overrides: object) -> MagicMock:
    players = MagicMock()
    players.get_by_id = AsyncMock(return_value=overrides.get("player", _player()))
    return players


def _cog(*, shop: MagicMock | None = None, players: MagicMock | None = None) -> ShopAdminCog:
    return ShopAdminCog(shop or _shop(), players or _players(), EmbedFactory())


def _interaction(user_id: int = 1) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock(spec=discord.Member, id=user_id)
    interaction.user.guild_permissions = MagicMock(administrator=True)
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock(return_value=MagicMock(spec=discord.Message))
    interaction.original_response = AsyncMock(return_value=MagicMock(spec=discord.Message))
    return interaction


async def test_category_upserts_and_reports() -> None:
    shop = _shop(category=_category(key="elite"))
    cog = _cog(shop=shop)
    interaction = _interaction()

    callback: Any = ShopAdminCog.category.callback
    await callback(cog, interaction, "elite", "🟢 Мелкие товары", "Описание", 0, True)

    shop.upsert_category.assert_awaited_once_with(
        "elite", "🟢 Мелкие товары", description="Описание", sort_order=0, active=True
    )
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "elite" in (embed.description or "")


async def test_add_creates_an_item_with_the_chosen_effect() -> None:
    added = _item(name="Кофе", price_coins=75, effect_kind=EffectKind.XP_GRANT.value)
    shop = _shop(added_item=added)
    cog = _cog(shop=shop)
    interaction = _interaction()

    callback: Any = ShopAdminCog.add.callback
    await callback(
        cog,
        interaction,
        "small",
        "Кофе",
        "Бодрит",
        75,
        _effect_choice(EffectKind.XP_GRANT),
        "10-20",
        None,
        None,
        None,
        None,
        None,
        0,
    )

    shop.add_item.assert_awaited_once_with(
        "small",
        "Кофе",
        "Бодрит",
        75,
        EffectKind.XP_GRANT.value,
        effect_value="10-20",
        duration_days=None,
        uses=None,
        stock=None,
        per_player_limit=None,
        emoji=None,
        sort_order=0,
    )
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "Кофе" in (embed.description or "")


async def test_edit_fills_cleared_fields_from_the_current_item_when_not_provided() -> None:
    current = _item(
        effect_value="5", duration_days=3, uses=2, stock=10, per_player_limit=1, emoji="☕"
    )
    shop = _shop(get_item_return=current, updated_item=_item(price_coins=99))
    cog = _cog(shop=shop)
    interaction = _interaction()

    callback: Any = ShopAdminCog.edit.callback
    await callback(
        cog,
        interaction,
        1,  # товар
        None,  # категория
        None,  # название
        None,  # описание
        99,  # цена
        None,  # эффект
        None,  # значение_эффекта
        None,  # дней
        None,  # использований
        None,  # остаток
        None,  # лимит_на_игрока
        None,  # эмодзи
        None,  # порядок
        None,  # активен
        False,  # без_срока
        False,  # без_лимита_использований
        False,  # без_ограничения_остатка
        False,  # без_лимита_на_игрока
    )

    shop.update_item.assert_awaited_once_with(
        1,
        category_key=None,
        name=None,
        description=None,
        price_coins=99,
        effect_kind=None,
        effect_value="5",
        duration_days=3,
        uses=2,
        stock=10,
        per_player_limit=1,
        emoji="☕",
        sort_order=None,
        active=None,
    )


async def test_edit_without_flags_clears_duration_uses_stock_and_per_player_limit() -> None:
    current = _item(effect_value="5", duration_days=3, uses=2, stock=10, per_player_limit=1)
    shop = _shop(get_item_return=current)
    cog = _cog(shop=shop)
    interaction = _interaction()

    callback: Any = ShopAdminCog.edit.callback
    await callback(
        cog,
        interaction,
        1,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        True,  # без_срока
        True,  # без_лимита_использований
        True,  # без_ограничения_остатка
        True,  # без_лимита_на_игрока
    )

    kwargs = shop.update_item.call_args.kwargs
    assert kwargs["duration_days"] is None
    assert kwargs["uses"] is None
    assert kwargs["stock"] is None
    assert kwargs["per_player_limit"] is None


async def test_edit_reports_a_missing_item_without_calling_update() -> None:
    shop = _shop(get_item_return=None)
    cog = _cog(shop=shop)
    interaction = _interaction()

    callback: Any = ShopAdminCog.edit.callback
    await callback(cog, interaction, 999, *([None] * 13), False, False, False, False)

    shop.update_item.assert_not_called()
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "не найден" in (embed.description or "")


async def test_delete_removes_the_item_once_confirmed(monkeypatch: pytest.MonkeyPatch) -> None:
    shop = _shop(get_item_return=_item(name="Кофе"), deleted_item=_item(name="Кофе"))
    cog = _cog(shop=shop)
    interaction = _interaction()

    fake_view = MagicMock()
    fake_view.wait = AsyncMock()
    fake_view.confirmed = True
    monkeypatch.setattr(
        "stalbot.presentation.cogs.shop.admin_cog.ConfirmView", MagicMock(return_value=fake_view)
    )

    callback: Any = ShopAdminCog.delete.callback
    await callback(cog, interaction, 1)

    shop.delete_item.assert_awaited_once_with(1)
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "Кофе" in (embed.description or "")


async def test_delete_leaves_the_item_when_not_confirmed(monkeypatch: pytest.MonkeyPatch) -> None:
    shop = _shop(get_item_return=_item(name="Кофе"))
    cog = _cog(shop=shop)
    interaction = _interaction()

    fake_view = MagicMock()
    fake_view.wait = AsyncMock()
    fake_view.confirmed = False
    monkeypatch.setattr(
        "stalbot.presentation.cogs.shop.admin_cog.ConfirmView", MagicMock(return_value=fake_view)
    )

    callback: Any = ShopAdminCog.delete.callback
    await callback(cog, interaction, 1)

    shop.delete_item.assert_not_called()


async def test_refund_credits_back_and_reports_the_buyer() -> None:
    shop = _shop()
    shop.refund = AsyncMock(
        return_value=RefundResult(
            purchase=_purchase(),
            item=_item(name="Кофе"),
            refunded_coins=50,
            new_balance=550,
            reversed_effects=(),
        )
    )
    players = _players(player=_player(nick_display="Scaryyyyy"))
    cog = _cog(shop=shop, players=players)
    interaction = _interaction()

    callback: Any = ShopAdminCog.refund.callback
    await callback(cog, interaction, 1)

    shop.refund.assert_awaited_once_with(1, admin_id=1)
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "Scaryyyyy" in (embed.description or "")
    assert "550" in (embed.description or "")


async def test_category_autocomplete_filters_by_key_or_name() -> None:
    shop = _shop(categories=[_category(key="small"), _category(key="elite")])
    cog = _cog(shop=shop)
    interaction = _interaction()

    # Stacking three `.autocomplete()` decorators on one function confuses mypy's
    # inferred type for it (discord.py's own overloads) even though the plain
    # bound method still takes (interaction, current) at runtime.
    choices = await cog._category_autocomplete(interaction, "small")  # type: ignore[call-arg]

    assert [c.value for c in choices] == ["small"]
