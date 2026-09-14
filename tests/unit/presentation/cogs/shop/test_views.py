"""Tests for `stalbot.presentation.cogs.shop.views.ShopBrowseView` (заявка 13.09.2026 п.2)."""

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from stalbot.application.services.shop import PurchaseResult
from stalbot.domain.entities.shop import PlayerEffect, ShopCategory, ShopItem, ShopPurchase
from stalbot.domain.errors import InsufficientCoinsError
from stalbot.presentation.cogs.shop.views import ShopBrowseView
from stalbot.presentation.embeds.factory import EmbedFactory

_DISCORD_ID = 42


def _category(key: str = "small", name: str = "🟢 Мелкие товары") -> ShopCategory:
    return ShopCategory(key=key, name=name, description="Недорогие мелочи")


def _item(item_id: int = 1, *, price: int = 100, name: str = "Кофе") -> ShopItem:
    return ShopItem(
        id=item_id,
        category_key="small",
        name=name,
        name_norm=name.lower(),
        description="Бодрит",
        price_coins=price,
        effect_kind="manual",
    )


def _shop(*, items: list[ShopItem] | None = None) -> MagicMock:
    shop = MagicMock()
    shop.items = AsyncMock(return_value=items if items is not None else [_item()])
    return shop


def _view(shop: MagicMock, *, categories: list[ShopCategory] | None = None) -> ShopBrowseView:
    return ShopBrowseView(
        shop,
        categories if categories is not None else [_category()],
        author_id=1,
        discord_id=_DISCORD_ID,
        embeds=EmbedFactory(),
        balance=500,
    )


def _interaction() -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.response.edit_message = AsyncMock()
    interaction.message = MagicMock(spec=discord.Message, id=999)
    return interaction


async def test_initial_embed_lists_categories() -> None:
    view = _view(_shop())
    embed = view.embed
    assert "🟢 Мелкие товары" in (embed.description or "")
    assert "500" in (embed.description or "")


async def test_selecting_a_category_loads_its_items() -> None:
    item = _item(name="Кофе")
    shop = _shop(items=[item])
    view = _view(shop)
    interaction = _interaction()
    assert view._category_select is not None
    view._category_select._values = ["small"]

    await view._on_category_selected(interaction)

    shop.items.assert_awaited_once_with(category_key="small")
    embed = view.embed
    assert "Кофе" in (embed.description or "")
    interaction.response.edit_message.assert_awaited_once()


async def test_selecting_an_item_shows_its_detail() -> None:
    item = _item(name="Кофе", price=150)
    view = _view(_shop(items=[item]))
    interaction = _interaction()
    assert view._category_select is not None
    view._category_select._values = ["small"]
    await view._on_category_selected(interaction)

    assert view._item_select is not None
    view._item_select._values = [str(item.id)]
    await view._on_item_selected(interaction)

    embed = view.embed
    assert "150" in (embed.description or "")
    assert "Подтвердите" not in (embed.description or "")


async def test_buy_click_switches_to_confirming() -> None:
    item = _item()
    view = _view(_shop(items=[item]))
    view._item = item
    interaction = _interaction()

    await view._on_buy_clicked(interaction)

    assert view._confirming is True
    embed = view.embed
    assert "Подтвердите" in (embed.description or "")


async def test_cancel_returns_to_the_item_view_without_confirming() -> None:
    item = _item()
    view = _view(_shop(items=[item]))
    view._item = item
    view._confirming = True
    interaction = _interaction()

    await view._on_cancel(interaction)

    assert view._confirming is False
    embed = view.embed
    assert "Подтвердите" not in (embed.description or "")


async def test_confirm_buys_with_a_message_scoped_idempotency_key() -> None:
    item = _item(item_id=7)
    shop = _shop(items=[item])
    purchase = ShopPurchase(id=1, player_id=1, shop_item_id=7, price_coins=item.price_coins)
    effect = PlayerEffect(id=1, player_id=1, effect_kind="manual", purchase_id=1)
    shop.buy = AsyncMock(
        return_value=PurchaseResult(purchase=purchase, item=item, effect=effect, new_balance=400)
    )
    view = _view(shop, categories=[_category()])
    view._item = item
    view._confirming = True
    interaction = _interaction()

    await view._on_confirm(interaction)

    shop.buy.assert_awaited_once_with(_DISCORD_ID, 7, idempotency_key="shop_buy:999:7")
    interaction.response.edit_message.assert_awaited_once()
    embed = interaction.response.edit_message.call_args.kwargs["embed"]
    assert "400" in (embed.description or "")
    assert view.is_finished()


async def test_confirm_propagates_a_domain_error_for_the_views_own_error_handler() -> None:
    """`_on_confirm` itself must not swallow the error — `ErrorReportingView.on_error`
    (discord.py's own dispatch, not exercised by calling the callback directly) is what
    turns this into a user-facing embed."""
    item = _item()
    shop = _shop(items=[item])
    shop.buy = AsyncMock(side_effect=InsufficientCoinsError("не хватает Coins"))
    view = _view(shop)
    view._item = item
    view._confirming = True
    interaction = _interaction()

    with pytest.raises(InsufficientCoinsError):
        await view._on_confirm(interaction)

    interaction.response.edit_message.assert_not_called()
