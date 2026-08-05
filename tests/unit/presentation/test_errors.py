"""Tests for `stalbot.presentation.errors` (PLAN.md §12)."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import app_commands

from stalbot.domain.clock import GMT3
from stalbot.domain.errors import (
    AmountParseError,
    DomainError,
    ItemNotFoundError,
    NoTransactionsYetError,
    ProtectedRangeWriteError,
    SheetStructureError,
    TicketSessionNotFoundError,
)
from stalbot.presentation.embeds.factory import EmbedFactory
from stalbot.presentation.errors import _resolve_message, on_app_command_error, on_modal_error

_NOW = datetime(2026, 7, 31, 21, 45, tzinfo=GMT3)


def test_check_failure_maps_to_permission_denied() -> None:
    error = app_commands.CheckFailure("nope")
    assert _resolve_message(error, "abc123") == "Недостаточно прав для этого действия."


def test_command_on_cooldown_maps_to_a_retry_message_not_permission_denied() -> None:
    """SEC-5: `CommandOnCooldown` is itself a `CheckFailure` — must not be mistaken
    for one and told "insufficient permissions" when they just need to wait."""
    error = app_commands.CommandOnCooldown(app_commands.checks.Cooldown(1, 15.0), 12.3)
    message = _resolve_message(error, "abc123")
    assert "Недостаточно прав" not in message
    assert "12" in message


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


def test_no_transactions_yet_error_maps_to_its_message() -> None:
    wrapped = _wrap(NoTransactionsYetError("no deals yet"))
    assert (
        _resolve_message(wrapped, "abc123")
        == "Реферала можно указать только после первой сделки игрока."
    )


def test_ticket_session_not_found_error_maps_to_its_message() -> None:
    wrapped = _wrap(TicketSessionNotFoundError("no session for channel 1"))
    assert "Тикет не найден" in _resolve_message(wrapped, "abc123")


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


# --- PRES-1: infrastructure errors must never leak internal details ---------


def test_sheet_structure_error_does_not_leak_internal_details() -> None:
    wrapped = _wrap(SheetStructureError("missing sheet: 'Users'; block 'DataBase' mismatch"))
    message = _resolve_message(wrapped, "abc123")
    assert "Users" not in message
    assert "missing sheet" not in message
    assert "abc123" in message
    assert "Внутренняя ошибка" in message


def test_protected_range_write_error_does_not_leak_internal_details() -> None:
    wrapped = _wrap(
        ProtectedRangeWriteError(
            "column F on sheet 'DataBase' is formula-owned (attempted write to 'DataBase!F3')"
        )
    )
    message = _resolve_message(wrapped, "abc123")
    assert "DataBase!F3" not in message
    assert "formula-owned" not in message
    assert "abc123" in message


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


# --- SEC-4: Modal.on_submit errors go through the same convention -----------
# --- as app_commands' — a Modal's exception is never CommandInvokeError- ---
# --- wrapped, so `on_modal_error` maps the raw cause directly. -------------


async def test_modal_error_maps_a_known_domain_error(factory: EmbedFactory) -> None:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = MagicMock()
    interaction.response.is_done.return_value = False
    interaction.response.send_message = AsyncMock()

    await on_modal_error(interaction, AmountParseError("bad amount"), embeds=factory)

    embed = interaction.response.send_message.call_args.kwargs["embed"]
    assert "распознать сумму" in (embed.description or "")


async def test_modal_error_does_not_leak_internal_details(factory: EmbedFactory) -> None:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = MagicMock()
    interaction.response.is_done.return_value = False
    interaction.response.send_message = AsyncMock()

    await on_modal_error(
        interaction,
        SheetStructureError("missing sheet: 'Users'; block 'DataBase' mismatch"),
        embeds=factory,
    )

    embed = interaction.response.send_message.call_args.kwargs["embed"]
    description = embed.description or ""
    assert "Users" not in description
    assert "missing sheet" not in description
    assert "Внутренняя ошибка" in description


async def test_modal_error_shows_a_generic_message_for_an_unmapped_exception(
    factory: EmbedFactory,
) -> None:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = MagicMock()
    interaction.response.is_done.return_value = False
    interaction.response.send_message = AsyncMock()

    await on_modal_error(interaction, RuntimeError("boom"), embeds=factory)

    embed = interaction.response.send_message.call_args.kwargs["embed"]
    assert "Внутренняя ошибка" in (embed.description or "")


async def test_modal_error_uses_followup_when_already_responded(factory: EmbedFactory) -> None:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = MagicMock()
    interaction.response.is_done.return_value = True
    interaction.response.send_message = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()

    await on_modal_error(interaction, RuntimeError("boom"), embeds=factory)

    interaction.followup.send.assert_awaited_once()
    interaction.response.send_message.assert_not_called()
