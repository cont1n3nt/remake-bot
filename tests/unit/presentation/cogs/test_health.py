"""Tests for `stalbot.presentation.cogs.health.HealthCog` (PLAN.md §12, M11)."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from stalbot.application.dto.health_status import HealthStatus
from stalbot.infrastructure.cache.sync import SyncReport
from stalbot.presentation.cogs.health import HealthCog
from stalbot.presentation.embeds.factory import EmbedFactory


def _status(**overrides: object) -> HealthStatus:
    defaults: dict[str, object] = {
        "sheets_read_requests": 10,
        "sheets_write_requests": 3,
        "cache_hit_rate": 0.9,
        "cache_age_seconds": 45.0,
        "last_users_sync": SyncReport(
            users_synced=237,
            transactions_synced=64,
            transactions_skipped=0,
            formula_free_rows=620,
            duration_seconds=0.84,
        ),
        "last_items_sync": SyncReport(items_synced=219, duration_seconds=0.31),
        "audit_queue_size": 0,
        "ocr_sample_count": 12,
        "ocr_confirmed_sample_count": 3,
    }
    defaults.update(overrides)
    return HealthStatus(**defaults)  # type: ignore[arg-type]


def _cog(*, status: HealthStatus | None = None, started_at: datetime | None = None) -> HealthCog:
    health = MagicMock()
    health.snapshot = AsyncMock(return_value=status or _status())
    return HealthCog(
        health, EmbedFactory(), started_at=started_at or datetime(2026, 8, 3, 10, 0, tzinfo=UTC)
    )


def _interaction() -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


async def _call(cog: HealthCog, interaction: MagicMock) -> None:
    callback: Any = HealthCog.healthcheck.callback
    await callback(cog, interaction)


async def test_healthcheck_defers_ephemerally_and_replies_once() -> None:
    cog = _cog()
    interaction = _interaction()

    await _call(cog, interaction)

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    interaction.followup.send.assert_awaited_once()
    assert interaction.followup.send.call_args.kwargs["ephemeral"] is True


async def test_healthcheck_shows_sheets_and_cache_counters() -> None:
    cog = _cog(status=_status(sheets_read_requests=10, sheets_write_requests=3))
    interaction = _interaction()

    await _call(cog, interaction)

    embed = interaction.followup.send.call_args.kwargs["embed"]
    description = embed.description or ""
    assert "10 чтений" in description
    assert "3 записей" in description
    assert "90%" in description


async def test_healthcheck_shows_uptime() -> None:
    started_at = datetime(2026, 8, 3, 10, 0, tzinfo=UTC)
    cog = _cog(started_at=started_at)
    interaction = _interaction()

    await _call(cog, interaction)

    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "Uptime" in (embed.description or "")


async def test_healthcheck_shows_sync_report_details() -> None:
    cog = _cog(
        status=_status(
            last_users_sync=SyncReport(
                users_synced=237,
                transactions_synced=64,
                transactions_skipped=1,
                formula_free_rows=620,
                duration_seconds=0.84,
            ),
            last_items_sync=SyncReport(items_synced=219, duration_seconds=0.31),
        )
    )
    interaction = _interaction()

    await _call(cog, interaction)

    description = interaction.followup.send.call_args.kwargs["embed"].description or ""
    assert "237 польз." in description
    assert "64 сделок" in description
    assert "620" in description
    assert "219 предметов" in description


async def test_healthcheck_handles_never_synced_state() -> None:
    cog = _cog(
        status=_status(
            last_users_sync=None,
            last_items_sync=None,
            cache_age_seconds=None,
            cache_hit_rate=None,
        )
    )
    interaction = _interaction()

    await _call(cog, interaction)

    description = interaction.followup.send.call_args.kwargs["embed"].description or ""
    assert "ещё не выполнялся" in description
    assert "нет данных" in description
    assert "н/д" in description


async def test_healthcheck_shows_ocr_dataset_progress() -> None:
    cog = _cog(status=_status(ocr_sample_count=12, ocr_confirmed_sample_count=3))
    interaction = _interaction()

    await _call(cog, interaction)

    description = interaction.followup.send.call_args.kwargs["embed"].description or ""
    assert "12 / 150" in description
    assert "3 / 50" in description


async def test_healthcheck_shows_audit_queue_size() -> None:
    cog = _cog(status=_status(audit_queue_size=5))
    interaction = _interaction()

    await _call(cog, interaction)

    description = interaction.followup.send.call_args.kwargs["embed"].description or ""
    assert "Очередь аудита: 5" in description
