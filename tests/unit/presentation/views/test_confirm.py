"""Tests for `stalbot.presentation.views.confirm.ConfirmView`."""

from unittest.mock import AsyncMock, MagicMock

import discord

from stalbot.presentation.views.confirm import ConfirmView


def _interaction(user_id: int) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock(id=user_id)
    interaction.response = MagicMock()
    interaction.response.edit_message = AsyncMock()
    interaction.response.send_message = AsyncMock()
    return interaction


def test_starts_unanswered() -> None:
    view = ConfirmView(author_id=1)
    assert view.confirmed is None


async def test_confirm_button_sets_confirmed_true_and_disables_buttons() -> None:
    view = ConfirmView(author_id=1)
    interaction = _interaction(1)

    await view.confirm.callback(interaction)

    assert view.confirmed is True
    assert all(item.disabled for item in view.children if isinstance(item, discord.ui.Button))
    interaction.response.edit_message.assert_awaited_once()


async def test_cancel_button_sets_confirmed_false_and_disables_buttons() -> None:
    view = ConfirmView(author_id=1)
    interaction = _interaction(1)

    await view.cancel.callback(interaction)

    assert view.confirmed is False
    assert all(item.disabled for item in view.children if isinstance(item, discord.ui.Button))
    interaction.response.edit_message.assert_awaited_once()


async def test_interaction_check_allows_the_author() -> None:
    view = ConfirmView(author_id=1)
    assert await view.interaction_check(_interaction(1)) is True


async def test_interaction_check_rejects_other_users() -> None:
    view = ConfirmView(author_id=1)
    interaction = _interaction(999)

    allowed = await view.interaction_check(interaction)

    assert allowed is False
    interaction.response.send_message.assert_awaited_once()


async def test_on_timeout_disables_buttons_and_edits_the_stored_message() -> None:
    view = ConfirmView(author_id=1)
    message = MagicMock(spec=discord.Message)
    message.edit = AsyncMock()
    view.message = message

    await view.on_timeout()

    assert view.confirmed is None
    assert all(item.disabled for item in view.children if isinstance(item, discord.ui.Button))
    message.edit.assert_awaited_once_with(view=view)


async def test_on_timeout_is_a_no_op_without_a_stored_message() -> None:
    view = ConfirmView(author_id=1)
    await view.on_timeout()  # must not raise
