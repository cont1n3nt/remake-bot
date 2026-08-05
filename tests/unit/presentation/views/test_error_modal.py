"""Tests for `stalbot.presentation.views.error_modal.ErrorReportingModal` (SEC-4)."""

from unittest.mock import AsyncMock, MagicMock

import discord

from stalbot.domain.errors import AmountParseError
from stalbot.presentation.embeds.factory import EmbedFactory
from stalbot.presentation.views.error_modal import ErrorReportingModal


class _Modal(ErrorReportingModal):
    def __init__(self, embeds: EmbedFactory) -> None:
        super().__init__(title="test", embeds=embeds)


def _interaction() -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = MagicMock()
    interaction.response.is_done.return_value = False
    interaction.response.send_message = AsyncMock()
    return interaction


async def test_on_error_sends_the_mapped_message_for_a_domain_error() -> None:
    """The default discord.py Modal.on_error just logs and returns — this must not."""
    modal = _Modal(EmbedFactory())
    interaction = _interaction()

    await modal.on_error(interaction, AmountParseError("bad amount"))

    interaction.response.send_message.assert_awaited_once()
    embed = interaction.response.send_message.call_args.kwargs["embed"]
    assert "распознать сумму" in (embed.description or "")


async def test_on_error_does_not_raise_for_an_unmapped_exception() -> None:
    modal = _Modal(EmbedFactory())
    interaction = _interaction()

    await modal.on_error(interaction, RuntimeError("boom"))  # must not raise

    embed = interaction.response.send_message.call_args.kwargs["embed"]
    assert "Внутренняя ошибка" in (embed.description or "")
