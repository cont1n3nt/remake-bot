"""Tests for `stalbot.presentation.bot` that do not require a live connection."""

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from stalbot.application.services.audit import AuditService
from stalbot.config.settings import Settings
from stalbot.infrastructure.cache.db import CacheDb
from stalbot.infrastructure.sheets.client import SheetsClient
from stalbot.presentation.bot import StalbotBot, _channel_display, _format_arguments
from stalbot.presentation.embeds.factory import EmbedFactory


class _FakeSheetsClient:
    """Just enough of `SheetsClient` for `CacheSync` to run against empty data."""

    def __init__(self) -> None:
        self.validate_calls = 0

    async def validate_layout(self) -> None:
        self.validate_calls += 1

    async def batch_get(self, ranges: list[str]) -> dict[str, list[list[object]]]:
        return {}

    async def read_formula_extent(self, ref: str) -> int:
        return 1000


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


def _bot(settings: Settings, *, embed_factory: EmbedFactory | None = None) -> StalbotBot:
    """Build a `StalbotBot` with lazy (network-untouched) cache/Sheets collaborators."""
    return StalbotBot(
        settings,
        embed_factory=embed_factory or EmbedFactory(),
        cache_db=CacheDb(settings.cache_db_path),
        sheets_client=SheetsClient(settings),
    )


def test_construction_wires_embed_factory_and_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch)
    factory = EmbedFactory()
    bot = _bot(settings, embed_factory=factory)

    assert bot.settings is settings
    assert bot.embed_factory is factory
    assert bot.audit_service is None
    assert bot.cache_sync is None


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
    bot = _bot(settings)
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
    bot = _bot(settings)
    interaction = MagicMock(spec=discord.Interaction)
    command = MagicMock(spec=discord.app_commands.Command)

    await bot.on_app_command_completion(interaction, command)  # must not raise


async def test_close_stops_audit_service(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch)
    bot = _bot(settings)
    audit_service = MagicMock(spec=AuditService)
    audit_service.stop = AsyncMock()
    bot.audit_service = audit_service

    monkeypatch.setattr(discord.ext.commands.Bot, "close", AsyncMock())
    await bot.close()

    audit_service.stop.assert_awaited_once()


async def test_setup_cache_runs_startup_sync_before_starting_loops(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(monkeypatch)
    bot = _bot(settings)
    bot.cache_db = CacheDb(tmp_path / "cache.sqlite3")
    fake_client = _FakeSheetsClient()
    bot.sheets_client = fake_client  # type: ignore[assignment]
    bot.audit_service = MagicMock(spec=AuditService)

    await bot._setup_cache()

    assert fake_client.validate_calls == 1
    assert bot.cache_sync is not None
    assert bot.progression_service is not None
    assert bot._users_sync_loop is not None
    assert bot._users_sync_loop.is_running()
    assert bot._items_sync_loop is not None
    assert bot._items_sync_loop.is_running()
    assert bot._progression_loop is not None
    assert bot._progression_loop.is_running()

    bot._users_sync_loop.cancel()
    bot._items_sync_loop.cancel()
    bot._progression_loop.cancel()
    await bot.cache_db.close()


async def test_run_users_sync_delegates_to_cache_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch)
    bot = _bot(settings)
    cache_sync = MagicMock()
    cache_sync.sync_users_and_transactions = AsyncMock(
        return_value=SimpleNamespace(warnings=("⚠️ test",))
    )
    bot.cache_sync = cache_sync
    bot._send_warnings = AsyncMock()  # type: ignore[method-assign]

    await bot._run_users_sync()

    cache_sync.sync_users_and_transactions.assert_awaited_once()
    bot._send_warnings.assert_awaited_once_with(("⚠️ test",))


async def test_run_users_sync_is_a_no_op_before_cache_sync_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch)
    bot = _bot(settings)

    await bot._run_users_sync()  # must not raise


async def test_run_items_sync_delegates_to_cache_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch)
    bot = _bot(settings)
    cache_sync = MagicMock()
    cache_sync.sync_items = AsyncMock()
    bot.cache_sync = cache_sync

    await bot._run_items_sync()

    cache_sync.sync_items.assert_awaited_once()


async def test_run_progression_poll_delegates_to_progression_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch)
    bot = _bot(settings)
    progression_service = MagicMock()
    progression_service.sync = AsyncMock(return_value=[])
    bot.progression_service = progression_service

    await bot._run_progression_poll()

    progression_service.sync.assert_awaited_once_with()


async def test_run_progression_poll_is_a_no_op_before_progression_service_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch)
    bot = _bot(settings)

    await bot._run_progression_poll()  # must not raise


async def test_on_member_update_syncs_booster_flag_on_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch)
    bot = _bot(settings)
    progression_service = MagicMock()
    progression_service.sync_booster_flag = AsyncMock()
    bot.progression_service = progression_service

    before = SimpleNamespace(premium_since=None)
    after = SimpleNamespace(premium_since=datetime.now(UTC), id=42)

    await bot.on_member_update(before, after)  # type: ignore[arg-type]

    progression_service.sync_booster_flag.assert_awaited_once_with(42, True)


async def test_on_member_update_is_a_no_op_when_boost_status_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch)
    bot = _bot(settings)
    progression_service = MagicMock()
    progression_service.sync_booster_flag = AsyncMock()
    bot.progression_service = progression_service

    same_time = datetime.now(UTC)
    before = SimpleNamespace(premium_since=same_time)
    after = SimpleNamespace(premium_since=same_time, id=42)

    await bot.on_member_update(before, after)  # type: ignore[arg-type]

    progression_service.sync_booster_flag.assert_not_called()


async def test_on_member_update_is_a_no_op_before_progression_service_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch)
    bot = _bot(settings)
    before = SimpleNamespace(premium_since=None)
    after = SimpleNamespace(premium_since=datetime.now(UTC), id=42)

    await bot.on_member_update(before, after)  # type: ignore[arg-type]  # must not raise


async def test_send_warnings_is_a_no_op_for_empty_warnings(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch)
    bot = _bot(settings)
    monkeypatch.setattr(bot, "get_channel", MagicMock(return_value=MagicMock()))

    await bot._send_warnings(())  # must not raise, and must not touch get_channel's result


async def test_send_warnings_sends_one_embed_per_message(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch)
    bot = _bot(settings)
    channel = MagicMock(spec=discord.abc.Messageable)
    channel.send = AsyncMock()
    monkeypatch.setattr(bot, "get_channel", MagicMock(return_value=channel))

    await bot._send_warnings(("⚠️ first", "⚠️ second"))

    assert channel.send.await_count == 2


async def test_send_warnings_is_a_no_op_when_channel_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch)
    bot = _bot(settings)
    monkeypatch.setattr(bot, "get_channel", MagicMock(return_value=None))

    await bot._send_warnings(("⚠️ test",))  # must not raise


async def test_on_ready_flushes_startup_warnings_once(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch)
    bot = _bot(settings)
    bot._startup_warnings = ("⚠️ test",)
    bot._send_warnings = AsyncMock()  # type: ignore[method-assign]

    await bot.on_ready()

    bot._send_warnings.assert_awaited_once_with(("⚠️ test",))
    assert len(bot._startup_warnings) == 0


async def test_on_ready_refreshes_emoji_cache_when_guild_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch)
    bot = _bot(settings)
    emoji = MagicMock(name="topot")
    guild = SimpleNamespace(emojis=[emoji])
    monkeypatch.setattr(bot, "get_guild", MagicMock(return_value=guild))

    await bot.on_ready()

    assert bot.emoji_resolver._by_name  # populated from guild.emojis


async def test_on_ready_leaves_emoji_cache_empty_without_a_guild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch)
    bot = _bot(settings)
    monkeypatch.setattr(bot, "get_guild", MagicMock(return_value=None))

    await bot.on_ready()  # must not raise

    assert bot.emoji_resolver._by_name == {}


async def test_on_guild_emojis_update_refreshes_the_matching_guild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch)
    bot = _bot(settings)
    guild = SimpleNamespace(id=settings.guild_id)
    emoji = MagicMock()
    emoji.name = "tail"

    await bot.on_guild_emojis_update(guild, [], [emoji])  # type: ignore[arg-type]

    assert bot.emoji_resolver.resolve("tail") is not None


async def test_on_guild_emojis_update_ignores_other_guilds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch)
    bot = _bot(settings)
    other_guild = SimpleNamespace(id=settings.guild_id + 1)
    emoji = MagicMock()
    emoji.name = "tail"

    await bot.on_guild_emojis_update(other_guild, [], [emoji])  # type: ignore[arg-type]

    assert bot.emoji_resolver.resolve("tail") is None
