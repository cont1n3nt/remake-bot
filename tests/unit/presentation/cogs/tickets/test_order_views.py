"""Tests for `stalbot.presentation.cogs.tickets.order_views` (PLAN.md §11.6)."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import discord

from stalbot.domain.entities.item import Item
from stalbot.domain.enums import ItemCategory
from stalbot.presentation.cogs.tickets.order_views import (
    BoostMultiSelectView,
    OrderEditorView,
    OrderSummaryView,
)
from stalbot.presentation.embeds.factory import EmbedFactory


def _item(item_id: int, name: str) -> Item:
    return Item(
        id=item_id,
        name=name,
        category=ItemCategory.BOOST,
        price_buy=None,
        price_sell=Decimal(1000),
        emoji=None,
        updated_at=None,
        row=item_id + 2,
    )


def _custom_id(item: discord.ui.Item[OrderEditorView]) -> str | None:
    return item.custom_id if isinstance(item, discord.ui.Button | discord.ui.Select) else None


def _interaction(user_id: int = 1) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock(id=user_id)
    interaction.response = MagicMock()
    interaction.response.edit_message = AsyncMock()
    interaction.response.send_message = AsyncMock()
    return interaction


# -- OrderEditorView ------------------------------------------------------


def _order_editor_handlers() -> dict[str, AsyncMock]:
    return {
        "on_select": AsyncMock(),
        "on_plus": AsyncMock(),
        "on_minus": AsyncMock(),
        "on_input_qty": AsyncMock(),
        "on_delete": AsyncMock(),
        "on_add": AsyncMock(),
        "on_confirm": AsyncMock(),
    }


def test_order_editor_view_has_deterministic_custom_ids() -> None:
    handlers = _order_editor_handlers()
    view = OrderEditorView([discord.SelectOption(label="Топот", value="1")], **handlers)

    custom_ids = {_custom_id(item) for item in view.children}
    assert custom_ids == {
        "order:select",
        "order:qty:minus",
        "order:qty:plus",
        "order:qty:input",
        "order:qty:delete",
        "order:add",
        "order:confirm",
    }


def test_order_editor_view_shows_a_placeholder_when_empty() -> None:
    view = OrderEditorView([], **_order_editor_handlers())

    select = next(item for item in view.children if _custom_id(item) == "order:select")
    assert isinstance(select, discord.ui.Select)
    assert select.options[0].value == "none"


async def test_order_editor_select_invokes_on_select_with_the_item_id() -> None:
    handlers = _order_editor_handlers()
    view = OrderEditorView([discord.SelectOption(label="Топот", value="1")], **handlers)
    select = next(item for item in view.children if _custom_id(item) == "order:select")
    assert isinstance(select, discord.ui.Select)
    select._values = ["1"]
    interaction = _interaction()

    await select.callback(interaction)

    handlers["on_select"].assert_awaited_once_with(interaction, 1)


async def test_order_editor_select_ignores_the_placeholder_value() -> None:
    handlers = _order_editor_handlers()
    view = OrderEditorView([], **handlers)
    select = next(item for item in view.children if _custom_id(item) == "order:select")
    assert isinstance(select, discord.ui.Select)
    select._values = ["none"]

    await select.callback(_interaction())

    handlers["on_select"].assert_not_called()


async def test_order_editor_buttons_delegate_to_their_handlers() -> None:
    handlers = _order_editor_handlers()
    view = OrderEditorView([], **handlers)
    interaction = _interaction()

    for custom_id, handler in (
        ("order:qty:minus", handlers["on_minus"]),
        ("order:qty:plus", handlers["on_plus"]),
        ("order:qty:input", handlers["on_input_qty"]),
        ("order:qty:delete", handlers["on_delete"]),
        ("order:add", handlers["on_add"]),
        ("order:confirm", handlers["on_confirm"]),
    ):
        button = next(item for item in view.children if _custom_id(item) == custom_id)
        assert isinstance(button, discord.ui.Button)
        await button.callback(interaction)
        handler.assert_awaited_once_with(interaction)


# -- OrderSummaryView ------------------------------------------------------


def _order_summary_handlers() -> dict[str, AsyncMock]:
    return {"on_edit": AsyncMock(), "on_complete": AsyncMock()}


def test_order_summary_view_has_deterministic_custom_ids() -> None:
    view = OrderSummaryView(**_order_summary_handlers())

    custom_ids = {_custom_id(item) for item in view.children}
    assert custom_ids == {"order:edit", "order:complete"}


async def test_order_summary_buttons_delegate_to_their_handlers() -> None:
    handlers = _order_summary_handlers()
    view = OrderSummaryView(**handlers)
    interaction = _interaction()

    for custom_id, handler in (
        ("order:edit", handlers["on_edit"]),
        ("order:complete", handlers["on_complete"]),
    ):
        button = next(item for item in view.children if _custom_id(item) == custom_id)
        assert isinstance(button, discord.ui.Button)
        await button.callback(interaction)
        handler.assert_awaited_once_with(interaction)


# -- BoostMultiSelectView --------------------------------------------------


def _multiselect(
    items: list[Item], selected: frozenset[int], *, on_change: AsyncMock | None = None
) -> BoostMultiSelectView:
    return BoostMultiSelectView(
        items,
        selected,
        author_id=1,
        embeds=EmbedFactory(),
        on_change=on_change or AsyncMock(return_value=frozenset()),
    )


def test_multiselect_first_page_shows_up_to_25_options() -> None:
    items = [_item(i, f"Boost {i}") for i in range(30)]
    view = _multiselect(items, frozenset())

    assert len(view._select.options) == 25
    assert view.previous_page.disabled is True
    assert view.next_page.disabled is False


def test_multiselect_marks_already_selected_options_as_default() -> None:
    items = [_item(1, "Топот"), _item(2, "Ускорение")]
    view = _multiselect(items, frozenset({2}))

    options = {opt.value: opt.default for opt in view._select.options}
    assert options == {"1": False, "2": True}


def test_multiselect_shows_the_quantity_for_selected_items() -> None:
    items = [_item(1, "Топот"), _item(2, "Ускорение")]
    view = BoostMultiSelectView(
        items,
        frozenset({1}),
        author_id=1,
        embeds=EmbedFactory(),
        on_change=AsyncMock(return_value=frozenset()),
        quantities={1: 3},
    )

    labels = {opt.value: opt.label for opt in view._select.options}
    assert labels["1"] == "Топот — 3 шт."
    assert labels["2"] == "Ускорение"


async def test_multiselect_change_shows_the_default_quantity_for_a_newly_added_item() -> None:
    items = [_item(1, "Топот")]
    view = _multiselect(items, frozenset())
    view._select._values = ["1"]

    await view._handle_change(_interaction())

    assert view._select.options[0].label == "Топот — 1 шт."


async def test_multiselect_next_page_preserves_selection() -> None:
    items = [_item(i, f"Boost {i}") for i in range(30)]
    view = _multiselect(items, frozenset({0}))

    await view.next_page.callback(_interaction())

    assert len(view._select.options) == 5
    assert view.next_page.disabled is True


async def test_multiselect_change_reconciles_the_current_page_and_calls_on_change() -> None:
    items = [_item(1, "Топот"), _item(2, "Ускорение")]
    on_change = AsyncMock(return_value=frozenset())
    view = _multiselect(items, frozenset(), on_change=on_change)
    view._select._values = ["1"]
    interaction = _interaction()

    await view._handle_change(interaction)

    on_change.assert_awaited_once()
    (called_interaction, page_items, chosen_ids), _ = on_change.call_args
    assert called_interaction is interaction
    assert {item.id for item in page_items} == {1, 2}
    assert chosen_ids == frozenset({1})
    interaction.response.edit_message.assert_awaited_once()


async def test_multiselect_change_acks_before_calling_on_change() -> None:
    """TICK-9: `_on_change` can be slow (it posts/edits the public order-editor message) —
    the interaction must be acked first, or it can expire before the ack ever goes out."""
    order: list[str] = []
    interaction = _interaction()

    async def slow_on_change(
        _interaction: MagicMock, _page_items: object, _chosen_ids: object
    ) -> frozenset[int]:
        order.append("on_change")
        return frozenset()

    async def tracking_edit_message(**_kwargs: object) -> None:
        order.append("ack")

    interaction.response.edit_message = tracking_edit_message
    view = _multiselect([_item(1, "Топот")], frozenset(), on_change=slow_on_change)
    view._select._values = ["1"]

    await view._handle_change(interaction)

    assert order == ["ack", "on_change"]


async def test_multiselect_change_rolls_back_and_warns_about_rejected_items() -> None:
    """TICK-5: an item `_on_change` reports as rejected must not stay checked locally."""
    items = [_item(1, "Топот"), _item(2, "Ускорение")]
    on_change = AsyncMock(return_value=frozenset({2}))
    view = _multiselect(items, frozenset(), on_change=on_change)
    view._select._values = ["1", "2"]
    interaction = _interaction()

    await view._handle_change(interaction)

    options = {opt.value: opt.default for opt in view._select.options}
    assert options == {"1": True, "2": False}
    interaction.edit_original_response.assert_awaited_once()
    embed = interaction.edit_original_response.call_args.kwargs["embed"]
    assert "Не добавлено: 1" in (embed.description or "")


async def test_multiselect_change_skips_the_follow_up_edit_when_nothing_is_rejected() -> None:
    on_change = AsyncMock(return_value=frozenset())
    view = _multiselect([_item(1, "Топот")], frozenset(), on_change=on_change)
    view._select._values = ["1"]
    interaction = _interaction()

    await view._handle_change(interaction)

    interaction.edit_original_response.assert_not_called()


async def test_multiselect_change_updates_local_selection_state() -> None:
    items = [_item(1, "Топот"), _item(2, "Ускорение")]
    view = _multiselect(items, frozenset({1, 2}))
    view._select._values = ["1"]  # user unchecked item 2 on this page

    await view._handle_change(_interaction())

    options = {opt.value: opt.default for opt in view._select.options}
    assert options == {"1": True, "2": False}


def test_multiselect_status_embed_reports_the_selected_count() -> None:
    view = _multiselect([_item(1, "Топот")], frozenset({1}))

    embed = view.status_embed()

    assert "1" in (embed.description or "")


async def test_multiselect_interaction_check_rejects_other_users() -> None:
    view = _multiselect([_item(1, "Топот")], frozenset())
    interaction = _interaction(user_id=999)

    allowed = await view.interaction_check(interaction)

    assert allowed is False


async def test_multiselect_on_timeout_disables_controls() -> None:
    view = _multiselect([_item(1, "Топот")], frozenset())
    message = MagicMock(spec=discord.Message)
    message.edit = AsyncMock()
    view.message = message

    await view.on_timeout()

    assert view._select.disabled is True
    message.edit.assert_awaited_once_with(view=view)
