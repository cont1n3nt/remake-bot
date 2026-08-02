"""Tests for `stalbot.presentation.errors` (PLAN.md §12)."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import app_commands

from stalbot.domain.clock import GMT3
from stalbot.domain.errors import AmountParseError, DomainError, ItemNotFoundError
from stalbot.presentation.embeds.factory import EmbedFactory
from stalbot.presentation.errors import _resolve_message, on_app_command_error

_NOW = datetime(2026, 7, 31, 21, 45, tzinfo=GMT3)


def test_check_failure_maps_to_permission_denied() -> None:
    error = app_commands.CheckFailure("nope")
    assert _resolve_message(error, "abc123") == "Недостаточно прав для этого действия."


def _wrap(original: Exception) -> app_commands.CommandInvokeError:
    """Simulate how discord.py wraps a callback exception (`raise ... from e`)."""
    command = MagicMock()
    command.name = "add"
    try:
        raise app_commands.CommandInvokeError(command, original) from original
    except app_commands.CommandInvokeError as exc:
        return exc


def test_known_domain_error_maps_to_its_message() -> None:
    wrapped = _wrap(AmountParseError("bad amount"))
    assert "распознать сумму" in _resolve_message(wrapped, "abc123")


def test_known_domain_error_wrapped_in_command_invoke_error() -> None:
    wrapped = _wrap(ItemNotFoundError("no such item"))
    assert _resolve_message(wrapped, "abc123") == "Предмет не найден в базе."


def test_unlisted_domain_error_falls_back_to_str() -> None:
    class _CustomDomainError(DomainError):
        pass

    wrapped = _wrap(_CustomDomainError("something specific went wrong"))
    assert _resolve_message(wrapped, "abc123") == "something specific went wrong"


def test_unknown_error_shows_trace_id() -> None:
    wrapped = _wrap(RuntimeError("boom"))
    message = _resolve_message(wrapped, "abc123")
    assert "abc123" in message
    assert "Внутренняя ошибка" in message


class _FakeClock:
    def now(self) -> datetime:
        return _NOW


@pytest.fixture
def factory() -> EmbedFactory:
    return EmbedFactory(clock=_FakeClock())


async def test_sends_ephemeral_response_when_not_yet_responded(factory: EmbedFactory) -> None:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = MagicMock()
    interaction.response.is_done.return_value = False
    interaction.response.send_message = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()

    await on_app_command_error(interaction, app_commands.CheckFailure("nope"), embeds=factory)

    interaction.response.send_message.assert_awaited_once()
    interaction.followup.send.assert_not_called()
    _, kwargs = interaction.response.send_message.call_args
    assert kwargs["ephemeral"] is True


async def test_uses_followup_when_already_responded(factory: EmbedFactory) -> None:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = MagicMock()
    interaction.response.is_done.return_value = True
    interaction.response.send_message = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()

    await on_app_command_error(interaction, app_commands.CheckFailure("nope"), embeds=factory)

    interaction.followup.send.assert_awaited_once()
    interaction.response.send_message.assert_not_called()
