"""Tests for `stalbot.presentation.cogs.shop.cog.ShopCog` (заявка 13.09.2026 п.2)."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from stalbot.domain.entities.shop import ShopCategory, ShopItem, ShopPurchase
from stalbot.presentation.cogs.shop.cog import ShopCog
from stalbot.presentation.cogs.shop.views import ShopBrowseView
from stalbot.presentation.embeds.factory import EmbedFactory
from stalbot.presentation.views.paginated_embed import PaginatedEmbedView


def _category(key: str = "small") -> ShopCategory:
    return ShopCategory(key=key, name="🟢 Мелкие товары")


def _item(item_id: int = 1, *, name: str = "Кофе") -> ShopItem:
    return ShopItem(
        id=item_id,
        category_key="small",
        name=name,
        name_norm=name.lower(),
        description="Бодрит",
        price_coins=50,
        effect_kind="manual",
    )


def _purchase(purchase_id: int, item_id: int) -> ShopPurchase:
    return ShopPurchase(id=purchase_id, player_id=1, shop_item_id=item_id, price_coins=50)


def _shop(**overrides: object) -> MagicMock:
    shop = MagicMock()
    shop.balance = AsyncMock(return_value=overrides.get("balance", 500))
    shop.categories = AsyncMock(return_value=overrides.get("categories", [_category()]))
    shop.my_purchases = AsyncMock(return_value=overrides.get("my_purchases", []))
    shop.my_live_effects = AsyncMock(return_value=overrides.get("my_live_effects", []))
    shop.get_item = AsyncMock(return_value=overrides.get("get_item_return", _item()))
    return shop


def _interaction() -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock(id=42)
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock(return_value=MagicMock(spec=discord.Message))
    return interaction


async def test_shop_opens_the_browse_view_with_the_players_balance() -> None:
    shop = _shop(balance=750, categories=[_category()])
    cog = ShopCog(shop, EmbedFactory())
    interaction = _interaction()

    callback: Any = ShopCog.shop.callback
    await callback(cog, interaction)

    shop.balance.assert_awaited_once_with(42)
    kwargs = interaction.followup.send.call_args.kwargs
    assert isinstance(kwargs["view"], ShopBrowseView)
    assert "750" in (kwargs["embed"].description or "")


async def test_shop_purchases_reports_empty_history_and_effects_in_two_messages() -> None:
    shop = _shop(my_purchases=[], my_live_effects=[])
    cog = ShopCog(shop, EmbedFactory())
    interaction = _interaction()

    callback: Any = ShopCog.shop_purchases.callback
    await callback(cog, interaction)

    assert interaction.followup.send.await_count == 2
    first_embed = interaction.followup.send.await_args_list[0].kwargs["embed"]
    second_embed = interaction.followup.send.await_args_list[1].kwargs["embed"]
    assert "Пока нет покупок" in (first_embed.description or "")
    assert "ничего не активно" in (second_embed.description or "")


async def test_shop_purchases_resolves_item_names_and_paginates_past_the_page_size() -> None:
    items = {1: _item(1, name="Кофе"), 2: _item(2, name="Чай")}
    purchases = [_purchase(i, item_id=1 if i % 2 else 2) for i in range(1, 12)]
    shop = _shop(my_purchases=purchases, my_live_effects=[])
    shop.get_item = AsyncMock(side_effect=lambda item_id: items[item_id])
    cog = ShopCog(shop, EmbedFactory())
    interaction = _interaction()

    callback: Any = ShopCog.shop_purchases.callback
    await callback(cog, interaction)

    # 11 purchases over 2 unique items -> 2 get_item calls (cached per id), not 11.
    assert shop.get_item.await_count == 2
    kwargs = interaction.followup.send.await_args_list[0].kwargs
    assert isinstance(kwargs["view"], PaginatedEmbedView)
    assert "Кофе" in (kwargs["embed"].description or "")
