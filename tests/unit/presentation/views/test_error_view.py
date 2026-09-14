"""Tests for `stalbot.presentation.views.error_view.ErrorReportingView` (заявка 13.09.2026 п.2)."""

from unittest.mock import AsyncMock, MagicMock

import discord

from stalbot.domain.errors import InsufficientCoinsError
from stalbot.presentation.embeds.factory import EmbedFactory
from stalbot.presentation.views.error_view import ErrorReportingView


def _interaction(*, response_done: bool = False) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = MagicMock()
    interaction.response.is_done = MagicMock(return_value=response_done)
    interaction.response.send_message = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


async def test_on_error_sends_a_fresh_message_when_the_response_is_untouched() -> None:
    """A callback that raises before doing anything else — the common shop-button case."""
    view = ErrorReportingView(author_id=1, embeds=EmbedFactory())
    interaction = _interaction(response_done=False)

    await view.on_error(interaction, InsufficientCoinsError("не хватает Coins"), MagicMock())

    interaction.response.send_message.assert_awaited_once()
    embed = interaction.response.send_message.call_args.kwargs["embed"]
    assert "не хватает Coins" in (embed.description or "")
    interaction.followup.send.assert_not_called()


async def test_on_error_uses_followup_once_the_response_is_already_done() -> None:
    view = ErrorReportingView(author_id=1, embeds=EmbedFactory())
    interaction = _interaction(response_done=True)

    await view.on_error(interaction, ValueError("boom"), MagicMock())

    interaction.followup.send.assert_awaited_once()
    interaction.response.send_message.assert_not_called()
