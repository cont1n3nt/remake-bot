"""Tests for `stalbot.presentation.views.paginated_select.PaginatedItemSelect`."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import discord

from stalbot.domain.entities.item import Item
from stalbot.domain.enums import ItemCategory
from stalbot.presentation.views.paginated_select import PaginatedItemSelect


def _item(item_id: int, name: str) -> Item:
    return Item(
        id=item_id,
        name=name,
        category=ItemCategory.RESOURCE,
        price_buy=Decimal(1),
        price_sell=None,
        emoji=None,
        updated_at=None,
        row=item_id + 2,
    )


def _interaction(user_id: int) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock(id=user_id)
    interaction.response = MagicMock()
    interaction.response.edit_message = AsyncMock()
    interaction.response.send_message = AsyncMock()
    return interaction


async def test_first_page_shows_up_to_25_options() -> None:
    items = [_item(i, f"Item {i}") for i in range(30)]
    on_select = AsyncMock()
    view = PaginatedItemSelect(items, author_id=1, on_select=on_select)

    assert len(view._select.options) == 25
    assert view.previous_page.disabled is True
    assert view.next_page.disabled is False


async def test_next_page_shows_the_remainder() -> None:
    items = [_item(i, f"Item {i}") for i in range(30)]
    on_select = AsyncMock()
    view = PaginatedItemSelect(items, author_id=1, on_select=on_select)
    interaction = _interaction(1)

    await view.next_page.callback(interaction)

    assert len(view._select.options) == 5
    assert view.next_page.disabled is True
    interaction.response.edit_message.assert_awaited_once_with(view=view)


async def test_previous_page_stays_put_on_the_first_page() -> None:
    items = [_item(i, f"Item {i}") for i in range(5)]
    view = PaginatedItemSelect(items, author_id=1, on_select=AsyncMock())
    interaction = _interaction(1)

    await view.previous_page.callback(interaction)

    assert view.previous_page.disabled is True


async def test_selecting_invokes_the_callback_with_the_matching_item() -> None:
    items = [_item(1, "Топот"), _item(2, "Кристалл")]
    on_select = AsyncMock()
    view = PaginatedItemSelect(items, author_id=1, on_select=on_select)
    view._select._values = ["2"]
    interaction = _interaction(1)

    await view._handle_select(interaction)

    on_select.assert_awaited_once()
    (called_interaction, called_item), _ = on_select.call_args
    assert called_interaction is interaction
    assert called_item.id == 2


async def test_empty_catalog_shows_a_placeholder_option() -> None:
    view = PaginatedItemSelect([], author_id=1, on_select=AsyncMock())
    assert view._select.disabled is True
    assert view.page_count == 1


async def test_interaction_check_rejects_other_users() -> None:
    view = PaginatedItemSelect([_item(1, "Топот")], author_id=1, on_select=AsyncMock())
    interaction = _interaction(999)

    allowed = await view.interaction_check(interaction)

    assert allowed is False
    interaction.response.send_message.assert_awaited_once()


async def test_on_timeout_disables_controls_and_edits_the_stored_message() -> None:
    view = PaginatedItemSelect([_item(1, "Топот")], author_id=1, on_select=AsyncMock())
    message = MagicMock(spec=discord.Message)
    message.edit = AsyncMock()
    view.message = message

    await view.on_timeout()

    assert view._select.disabled is True
    message.edit.assert_awaited_once_with(view=view)


async def test_on_timeout_is_a_no_op_without_a_stored_message() -> None:
    view = PaginatedItemSelect([_item(1, "Топот")], author_id=1, on_select=AsyncMock())
    await view.on_timeout()  # must not raise
