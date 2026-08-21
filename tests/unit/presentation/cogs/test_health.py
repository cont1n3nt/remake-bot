"""Tests for `stalbot.presentation.cogs.health.HealthCog` (PLAN.md §12, M11).

sqlite_migration.md Э6: shows the database's own state instead of
Sheets/cache-sync counters.
"""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from stalbot.application.dto.health_status import HealthStatus
from stalbot.presentation.cogs.health import HealthCog
from stalbot.presentation.embeds.factory import EmbedFactory


def _status(**overrides: object) -> HealthStatus:
    defaults: dict[str, object] = {
        "schema_version": 7,
        "player_count": 253,
        "deal_count": 682,
        "last_deal_at": datetime(2026, 8, 3, 9, 45, tzinfo=UTC),
        "db_size_bytes": 2_097_152,
        "integrity_ok": True,
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


async def test_healthcheck_shows_schema_version_and_integrity() -> None:
    cog = _cog(status=_status(schema_version=7, integrity_ok=True))
    interaction = _interaction()

    await _call(cog, interaction)

    description = interaction.followup.send.call_args.kwargs["embed"].description or ""
    assert "v7" in description
    assert "целостность ок" in description


async def test_healthcheck_flags_a_failed_integrity_check() -> None:
    cog = _cog(status=_status(integrity_ok=False))
    interaction = _interaction()

    await _call(cog, interaction)

    description = interaction.followup.send.call_args.kwargs["embed"].description or ""
    assert "ЦЕЛОСТНОСТЬ НАРУШЕНА" in description


async def test_healthcheck_shows_uptime() -> None:
    started_at = datetime(2026, 8, 3, 10, 0, tzinfo=UTC)
    cog = _cog(started_at=started_at)
    interaction = _interaction()

    await _call(cog, interaction)

    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "Uptime" in (embed.description or "")


async def test_healthcheck_shows_player_and_deal_counts() -> None:
    cog = _cog(status=_status(player_count=253, deal_count=682))
    interaction = _interaction()

    await _call(cog, interaction)

    description = interaction.followup.send.call_args.kwargs["embed"].description or ""
    assert "Игроков: 253" in description
    assert "Сделок: 682" in description


async def test_healthcheck_shows_db_size() -> None:
    cog = _cog(status=_status(db_size_bytes=2_097_152))
    interaction = _interaction()

    await _call(cog, interaction)

    description = interaction.followup.send.call_args.kwargs["embed"].description or ""
    assert "2.0 МБ" in description


async def test_healthcheck_handles_no_deals_yet() -> None:
    cog = _cog(status=_status(last_deal_at=None))
    interaction = _interaction()

    await _call(cog, interaction)

    description = interaction.followup.send.call_args.kwargs["embed"].description or ""
    assert "нет данных" in description


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
