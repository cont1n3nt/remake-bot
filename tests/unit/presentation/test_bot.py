"""Tests for `stalbot.presentation.bot` that do not require a live connection."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from stalbot.application.services.audit import AuditService
from stalbot.config.settings import Settings
from stalbot.presentation.bot import StalbotBot, _channel_display, _format_arguments
from stalbot.presentation.embeds.factory import EmbedFactory


def _settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    for key, value in {
        "DISCORD_TOKEN": "fake-token",
        "GUILD_ID": "1475147129201627208",
        "LOG_CHANNEL_ID": "1518330495505797143",
        "REVIEWS_CHANNEL_ID": "1490342809075716237",
        "SPREADSHEET_ID": "1W3HDdzvnQ4Uzyn86RQUUp-hrzFgBikowtP5LBoq_Ov0",
    }.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_construction_wires_embed_factory_and_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch)
    factory = EmbedFactory()
    bot = StalbotBot(settings, embed_factory=factory)

    assert bot.settings is settings
    assert bot.embed_factory is factory
    assert bot.audit_service is None


def test_channel_display_uses_hash_prefix() -> None:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.channel = SimpleNamespace(name="ticket-0042")
    assert _channel_display(interaction) == "#ticket-0042"


def test_channel_display_falls_back_to_dm() -> None:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.channel = SimpleNamespace()
    assert _channel_display(interaction) == "DM"


def test_format_arguments_joins_namespace_values() -> None:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.namespace = SimpleNamespace(тип="Покупка", ник="Scaryyyyy")
    assert _format_arguments(interaction) == "тип=Покупка • ник=Scaryyyyy"


def test_format_arguments_empty_namespace() -> None:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.namespace = SimpleNamespace()
    assert _format_arguments(interaction) == ""


async def test_on_app_command_completion_records_audit_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch)
    bot = StalbotBot(settings, embed_factory=EmbedFactory())
    audit_service = MagicMock(spec=AuditService)
    bot.audit_service = audit_service

    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = SimpleNamespace(id=1)
    interaction.channel = SimpleNamespace(name="general")
    interaction.namespace = SimpleNamespace()
    interaction.created_at = datetime.now(UTC)

    command = MagicMock(spec=discord.app_commands.Command)
    command.qualified_name = "ping"

    await bot.on_app_command_completion(interaction, command)

    audit_service.record.assert_called_once()
    (event,) = audit_service.record.call_args.args
    assert event.command == "/ping"
    assert event.result == "Успешно"


async def test_on_app_command_completion_is_a_no_op_without_audit_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch)
    bot = StalbotBot(settings, embed_factory=EmbedFactory())
    interaction = MagicMock(spec=discord.Interaction)
    command = MagicMock(spec=discord.app_commands.Command)

    await bot.on_app_command_completion(interaction, command)  # must not raise


async def test_close_stops_audit_service(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch)
    bot = StalbotBot(settings, embed_factory=EmbedFactory())
    audit_service = MagicMock(spec=AuditService)
    audit_service.stop = AsyncMock()
    bot.audit_service = audit_service

    monkeypatch.setattr(discord.ext.commands.Bot, "close", AsyncMock())
    await bot.close()

    audit_service.stop.assert_awaited_once()
