"""Tests for `stalbot.presentation.cogs.catalog.CatalogCog` (PLAN.md §10.4-§10.5, §10.9).

`CatalogService`/`PricingService`/`ItemsCacheRepository` are mocked — their
own behavior is covered elsewhere; this file is about whether the cog wires
arguments correctly and builds the right embeds/views.
"""

from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
from discord import app_commands

from stalbot.application.dto.delete_item_result import DeleteItemResult
from stalbot.domain.entities.item import Item
from stalbot.domain.enums import ItemCategory
from stalbot.infrastructure.discord.emoji_resolver import EmojiResolver
from stalbot.presentation.cogs.catalog import CatalogCog, _PriceListView
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


def _cog(
    *,
    added_item: Item | None = None,
    delete_result: DeleteItemResult | None = None,
    all_items: list[Item] | None = None,
    by_category: dict[ItemCategory, list[Item]] | None = None,
    export_text: str = "price list\n",
    emojis: EmojiResolver | None = None,
) -> tuple[CatalogCog, MagicMock, MagicMock, MagicMock]:
    catalog = MagicMock()
    catalog.add_item = AsyncMock(return_value=added_item or _item())
    catalog.delete_item = AsyncMock(return_value=delete_result or DeleteItemResult(deleted=_item()))
    pricing = MagicMock()
    pricing.export_txt = AsyncMock(return_value=export_text)
    items = MagicMock()
    items.all = AsyncMock(return_value=all_items or [])
    by_category = by_category or {}
    items.by_category = AsyncMock(side_effect=lambda category: by_category.get(category, []))
    cog = CatalogCog(catalog, pricing, items, emojis or EmojiResolver(), EmbedFactory())
    return cog, catalog, pricing, items


def _interaction(*, user_id: int = 1) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock(return_value=MagicMock(spec=discord.Message))
    interaction.user = MagicMock(id=user_id)
    return interaction


def _category_choice(category: ItemCategory) -> app_commands.Choice[str]:
    return app_commands.Choice(name=category.value, value=category.value)


async def test_item_add_writes_item_and_reports_summary() -> None:
    item = _item(id=5, name="Кристалл", price_buy=Decimal(120000), emoji="crystal")
    cog, catalog, _pricing, _items = _cog(added_item=item)
    interaction = _interaction()

    callback: Any = CatalogCog.item_add.callback
    await callback(
        cog,
        interaction,
        "Кристалл",
        _category_choice(ItemCategory.RESOURCE),
        "120000",
        None,
        "crystal",
    )

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    catalog.add_item.assert_awaited_once_with(
        name="Кристалл",
        category=ItemCategory.RESOURCE,
        price_buy=Decimal(120000),
        price_sell=None,
        emoji="crystal",
    )
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "Кристалл" in (embed.description or "")
    assert "ID: 5" in (embed.description or "")


async def test_item_add_warns_when_emoji_not_found_on_server() -> None:
    item = _item(emoji="ghost")
    cog, _catalog, _pricing, _items = _cog(added_item=item, emojis=EmojiResolver())
    interaction = _interaction()

    callback: Any = CatalogCog.item_add.callback
    await callback(
        cog, interaction, "Хвост", _category_choice(ItemCategory.RESOURCE), None, None, "ghost"
    )

    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "не найдено на сервере" in (embed.description or "")


async def test_del_item_reports_deleted_item_summary() -> None:
    deleted = _item(id=2, name="Топот", category=ItemCategory.BOOST)
    cog, catalog, _pricing, _items = _cog(delete_result=DeleteItemResult(deleted=deleted))
    interaction = _interaction()

    callback: Any = CatalogCog.del_item.callback
    await callback(cog, interaction, 2)

    catalog.delete_item.assert_awaited_once_with(2)
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "Топот" in (embed.description or "")


async def test_del_item_warns_about_affected_boost_order_drafts() -> None:
    result = DeleteItemResult(deleted=_item(), affected_order_channels=[111, 222])
    cog, _catalog, _pricing, _items = _cog(delete_result=result)
    interaction = _interaction()

    callback: Any = CatalogCog.del_item.callback
    await callback(cog, interaction, 1)

    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "2 канале" in (embed.description or "")


async def test_del_item_autocomplete_delegates_to_item_choices() -> None:
    items = [_item(id=1, name="Топот"), _item(id=2, name="Кристалл")]
    cog, _catalog, _pricing, _items_repo = _cog(all_items=items)
    interaction = _interaction()

    autocomplete: Any = cog._del_item_autocomplete
    choices = await autocomplete(interaction, "топ")

    assert [c.value for c in choices] == [1]


async def test_price_list_sends_default_resource_page() -> None:
    resources = [_item(id=1, name="Хвост", category=ItemCategory.RESOURCE)]
    boosts = [_item(id=2, name="Топот", category=ItemCategory.BOOST, price_sell=Decimal(1))]
    cog, _catalog, _pricing, _items = _cog(
        by_category={ItemCategory.RESOURCE: resources, ItemCategory.BOOST: boosts}
    )
    interaction = _interaction()

    callback: Any = CatalogCog.price_list.callback
    await callback(cog, interaction)

    kwargs = interaction.followup.send.call_args.kwargs
    assert isinstance(kwargs["view"], _PriceListView)
    assert "Хвост" in (kwargs["embed"].description or "")


async def test_price_list_paginates_200_items_within_embed_limits() -> None:
    """PLAN.md §15 M11 DoD: embed limits verified at 200 items."""
    resources = [
        _item(id=i, name=f"Предмет {i}", category=ItemCategory.RESOURCE) for i in range(200)
    ]
    cog, _catalog, _pricing, _items = _cog(
        by_category={ItemCategory.RESOURCE: resources, ItemCategory.BOOST: []}
    )
    interaction = _interaction()

    callback: Any = CatalogCog.price_list.callback
    await callback(cog, interaction)

    view = interaction.followup.send.call_args.kwargs["view"]
    resource_pages = view._pages[ItemCategory.RESOURCE]
    assert len(resource_pages) == 14  # ceil(200 / 15)
    for page in resource_pages:
        assert len(page) <= 6000
        assert len(page.description or "") <= 4096


async def test_give_price_sends_a_txt_attachment() -> None:
    cog, _catalog, pricing, _items = _cog(export_text="# price list\n")
    interaction = _interaction()

    callback: Any = CatalogCog.give_price.callback
    await callback(cog, interaction)

    pricing.export_txt.assert_awaited_once()
    file = interaction.followup.send.call_args.kwargs["file"]
    assert isinstance(file, discord.File)
    assert file.filename == "price_list.txt"


# --- _PriceListView -------------------------------------------------------


def _embeds(*labels: str) -> list[discord.Embed]:
    return [discord.Embed(title=label) for label in labels]


def _view_interaction(user_id: int = 1) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock(id=user_id)
    interaction.response = MagicMock()
    interaction.response.edit_message = AsyncMock()
    interaction.response.send_message = AsyncMock()
    return interaction


def test_price_list_view_starts_on_resources_with_resources_button_disabled() -> None:
    view = _PriceListView(
        pages={
            ItemCategory.RESOURCE: _embeds("r1"),
            ItemCategory.BOOST: _embeds("b1"),
        },
        author_id=1,
    )
    assert view.current.title == "r1"
    assert view.show_resources.disabled is True
    assert view.show_boosts.disabled is False


async def test_price_list_view_switches_to_boosts() -> None:
    view = _PriceListView(
        pages={ItemCategory.RESOURCE: _embeds("r1"), ItemCategory.BOOST: _embeds("b1", "b2")},
        author_id=1,
    )
    interaction = _view_interaction()

    await view.show_boosts.callback(interaction)

    assert view.current.title == "b1"
    assert view.show_boosts.disabled is True
    assert view.next_page.disabled is False


async def test_price_list_view_paginates_within_a_category() -> None:
    view = _PriceListView(
        pages={ItemCategory.RESOURCE: _embeds("r1", "r2"), ItemCategory.BOOST: _embeds("b1")},
        author_id=1,
    )
    interaction = _view_interaction()

    await view.next_page.callback(interaction)

    assert view.current.title == "r2"
    assert view.next_page.disabled is True


async def test_price_list_view_switching_category_resets_page() -> None:
    view = _PriceListView(
        pages={ItemCategory.RESOURCE: _embeds("r1", "r2"), ItemCategory.BOOST: _embeds("b1")},
        author_id=1,
    )
    await view.next_page.callback(_view_interaction())
    assert view.current.title == "r2"

    await view.show_boosts.callback(_view_interaction())
    await view.show_resources.callback(_view_interaction())

    assert view.current.title == "r1"


async def test_price_list_view_interaction_check_rejects_other_users() -> None:
    view = _PriceListView(pages={ItemCategory.RESOURCE: _embeds("r1")}, author_id=1)
    interaction = _view_interaction(999)

    allowed = await view.interaction_check(interaction)

    assert allowed is False
    interaction.response.send_message.assert_awaited_once()


async def test_price_list_view_on_timeout_disables_buttons() -> None:
    view = _PriceListView(pages={ItemCategory.RESOURCE: _embeds("r1")}, author_id=1)
    message = MagicMock(spec=discord.Message)
    message.edit = AsyncMock()
    view.message = message

    await view.on_timeout()

    assert all(item.disabled for item in view.children if isinstance(item, discord.ui.Button))
    message.edit.assert_awaited_once_with(view=view)
