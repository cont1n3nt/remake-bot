"""Tests for `stalbot.presentation.views.base.AuthorLockedView` (PRES-4)."""

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from stalbot.presentation.views.base import AuthorLockedView


def _interaction(user_id: int) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock(id=user_id)
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    return interaction


class _View(AuthorLockedView):
    @discord.ui.button(label="x", style=discord.ButtonStyle.secondary)
    async def noop(
        self, interaction: discord.Interaction, button: discord.ui.Button["_View"]
    ) -> None:
        pass


async def test_interaction_check_allows_the_author() -> None:
    view = _View(author_id=1)
    assert await view.interaction_check(_interaction(1)) is True


async def test_interaction_check_rejects_other_users() -> None:
    view = _View(author_id=1)
    interaction = _interaction(999)

    allowed = await view.interaction_check(interaction)

    assert allowed is False
    interaction.response.send_message.assert_awaited_once()


async def test_on_timeout_disables_children_and_edits_the_stored_message() -> None:
    view = _View(author_id=1)
    message = MagicMock(spec=discord.Message)
    message.edit = AsyncMock()
    view.message = message

    await view.on_timeout()

    assert all(item.disabled for item in view.children if isinstance(item, discord.ui.Button))
    message.edit.assert_awaited_once_with(view=view)


async def test_on_timeout_is_a_no_op_without_a_stored_message() -> None:
    view = _View(author_id=1)
    await view.on_timeout()  # must not raise


@pytest.mark.parametrize("exception", [discord.NotFound, discord.Forbidden])
async def test_on_timeout_swallows_a_missing_or_forbidden_message(
    exception: type[discord.DiscordException],
) -> None:
    view = _View(author_id=1)
    message = MagicMock(spec=discord.Message)
    message.edit = AsyncMock(side_effect=exception(MagicMock(status=404, reason=""), "gone"))
    view.message = message

    await view.on_timeout()  # must not raise even though the message is gone

    assert all(item.disabled for item in view.children if isinstance(item, discord.ui.Button))
