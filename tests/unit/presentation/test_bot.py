"""Tests for `stalbot.presentation.bot` that do not require a live connection."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from stalbot.application.services.audit import AuditService
from stalbot.config.settings import Settings
from stalbot.infrastructure.cache.db import CacheDb
from stalbot.infrastructure.logging.trace import current_trace_id
from stalbot.presentation.bot import StalbotBot, _channel_display, _format_arguments
from stalbot.presentation.embeds.factory import EmbedFactory


def _settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    for key, value in {
        "DISCORD_TOKEN": "fake-token",
        "GUILD_ID": "1475147129201627208",
        "LOG_CHANNEL_ID": "1518330495505797143",
        "REVIEWS_CHANNEL_ID": "1490342809075716237",
    }.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)  # type: ignore[call-arg]


def _bot(settings: Settings, *, embed_factory: EmbedFactory | None = None) -> StalbotBot:
    """Build a `StalbotBot` with a lazy (network-untouched) cache collaborator."""
    return StalbotBot(
        settings,
        embed_factory=embed_factory or EmbedFactory(),
        cache_db=CacheDb(settings.cache_db_path),
    )


def test_construction_wires_embed_factory_and_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch)
    factory = EmbedFactory()
    bot = _bot(settings, embed_factory=factory)

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


async def test_close_awaits_a_cancelled_loops_task_before_closing_the_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PRES-9: `close()` must not close `cache_db` while a loop iteration is still in flight."""
    settings = _settings(monkeypatch)
    bot = _bot(settings)
    monkeypatch.setattr(discord.ext.commands.Bot, "close", AsyncMock())

    order: list[str] = []

    async def in_flight_iteration() -> None:
        await asyncio.sleep(0)  # simulate the loop still unwinding after cancel()
        order.append("loop_task_finished")

    task = asyncio.create_task(in_flight_iteration())
    fake_loop = MagicMock()
    fake_loop.get_task = MagicMock(return_value=task)
    bot._progression_loop = fake_loop

    async def tracking_cache_close() -> None:
        order.append("cache_closed")

    monkeypatch.setattr(bot.cache_db, "close", tracking_cache_close)

    await bot.close()

    assert order == ["loop_task_finished", "cache_closed"]
    fake_loop.cancel.assert_called_once()


async def test_close_still_closes_the_cache_when_a_loop_task_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`return_exceptions=True` must swallow a loop task's own failure, not propagate it."""
    settings = _settings(monkeypatch)
    bot = _bot(settings)
    monkeypatch.setattr(discord.ext.commands.Bot, "close", AsyncMock())

    async def failing_iteration() -> None:
        raise RuntimeError("boom")

    task = asyncio.create_task(failing_iteration())
    fake_loop = MagicMock()
    fake_loop.get_task = MagicMock(return_value=task)
    bot._progression_loop = fake_loop

    cache_close = AsyncMock()
    monkeypatch.setattr(bot.cache_db, "close", cache_close)

    await bot.close()  # must not raise despite the loop task's RuntimeError

    cache_close.assert_awaited_once()


async def test_setup_cache_wires_progression_service_and_starts_loops(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(monkeypatch)
    bot = _bot(settings)
    bot.cache_db = CacheDb(tmp_path / "cache.sqlite3")
    bot.audit_service = MagicMock(spec=AuditService)

    await bot._setup_cache()

    assert bot.progression_service is not None
    assert bot.health_service is not None
    assert bot._progression_loop is not None
    assert bot._progression_loop.is_running()
    assert bot._metrics_loop is not None
    assert bot._metrics_loop.is_running()

    bot._progression_loop.cancel()
    bot._metrics_loop.cancel()
    await bot.cache_db.close()


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


async def test_run_progression_poll_gets_a_fresh_trace_id_each_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch)
    bot = _bot(settings)
    progression_service = MagicMock()
    progression_service.sync = AsyncMock(return_value=[])
    bot.progression_service = progression_service

    await bot._run_progression_poll()
    first = current_trace_id()
    await bot._run_progression_poll()
    second = current_trace_id()

    assert first != second


async def test_run_metrics_log_gets_a_fresh_trace_id_each_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch)
    bot = _bot(settings)
    health_service = MagicMock()
    health_service.snapshot = AsyncMock(
        return_value=SimpleNamespace(
            schema_version=7,
            integrity_ok=True,
            player_count=0,
            deal_count=0,
            last_deal_at=None,
            db_size_bytes=0,
            audit_queue_size=0,
            ocr_sample_count=0,
            ocr_confirmed_sample_count=0,
        )
    )
    bot.health_service = health_service

    await bot._run_metrics_log()
    first = current_trace_id()
    await bot._run_metrics_log()
    second = current_trace_id()

    assert first != second


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
