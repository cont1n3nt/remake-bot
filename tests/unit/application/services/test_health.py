"""Tests for `stalbot.application.services.health.HealthService` (PLAN.md §12, M11).

sqlite_migration.md Э6: reports the database's own state (schema version,
row counts, integrity, size) instead of Sheets/cache-sync counters.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from stalbot.application.services.audit import AuditService
from stalbot.application.services.health import HealthService
from stalbot.infrastructure.cache.db import CacheDb
from stalbot.infrastructure.cache.repositories.deals import DealsRepository
from stalbot.infrastructure.cache.repositories.players import PlayersRepository
from stalbot.infrastructure.cache.repositories.screenshot_analyses import (
    ScreenshotAnalysesRepository,
)


def _cache_db(
    *, schema_version: int = 7, integrity_ok: bool = True, size_bytes: int = 1_048_576
) -> MagicMock:
    cache_db = MagicMock(spec=CacheDb)
    cache_db.schema_version = AsyncMock(return_value=schema_version)
    cache_db.integrity_ok = AsyncMock(return_value=integrity_ok)
    cache_db.size_bytes = MagicMock(return_value=size_bytes)
    return cache_db


def _players(*, count: int = 0) -> MagicMock:
    players = MagicMock(spec=PlayersRepository)
    players.count = AsyncMock(return_value=count)
    return players


def _deals(*, count: int = 0, last_occurred_at: datetime | None = None) -> MagicMock:
    deals = MagicMock(spec=DealsRepository)
    deals.count = AsyncMock(return_value=count)
    deals.last_occurred_at = AsyncMock(return_value=last_occurred_at)
    return deals


def _audit(*, queue_size: int = 0) -> MagicMock:
    audit = MagicMock(spec=AuditService)
    audit.queue_size = MagicMock(return_value=queue_size)
    return audit


def _screenshots(*, total: int = 0, confirmed: int = 0) -> MagicMock:
    screenshots = MagicMock(spec=ScreenshotAnalysesRepository)
    screenshots.count_all = AsyncMock(return_value=total)
    screenshots.count_with_confirmed_amount = AsyncMock(return_value=confirmed)
    return screenshots


async def test_snapshot_reports_schema_version_and_integrity() -> None:
    service = HealthService(
        _cache_db(schema_version=7, integrity_ok=True),
        _players(),
        _deals(),
        _audit(),
        _screenshots(),
    )

    status = await service.snapshot()

    assert status.schema_version == 7
    assert status.integrity_ok is True


async def test_snapshot_reports_a_failed_integrity_check() -> None:
    service = HealthService(
        _cache_db(integrity_ok=False), _players(), _deals(), _audit(), _screenshots()
    )

    status = await service.snapshot()

    assert status.integrity_ok is False


async def test_snapshot_reports_player_and_deal_counts() -> None:
    service = HealthService(
        _cache_db(), _players(count=253), _deals(count=682), _audit(), _screenshots()
    )

    status = await service.snapshot()

    assert status.player_count == 253
    assert status.deal_count == 682


async def test_snapshot_reports_last_deal_timestamp() -> None:
    last = datetime(2026, 8, 3, 11, 59, tzinfo=UTC)
    service = HealthService(
        _cache_db(), _players(), _deals(last_occurred_at=last), _audit(), _screenshots()
    )

    status = await service.snapshot()

    assert status.last_deal_at == last


async def test_snapshot_reports_no_last_deal_when_database_is_empty() -> None:
    service = HealthService(
        _cache_db(), _players(), _deals(last_occurred_at=None), _audit(), _screenshots()
    )

    status = await service.snapshot()

    assert status.last_deal_at is None


async def test_snapshot_reports_db_size() -> None:
    service = HealthService(
        _cache_db(size_bytes=2_097_152), _players(), _deals(), _audit(), _screenshots()
    )

    status = await service.snapshot()

    assert status.db_size_bytes == 2_097_152


async def test_snapshot_reports_audit_queue_size_and_ocr_counters() -> None:
    service = HealthService(
        _cache_db(),
        _players(),
        _deals(),
        _audit(queue_size=7),
        _screenshots(total=42, confirmed=9),
    )

    status = await service.snapshot()

    assert status.audit_queue_size == 7
    assert status.ocr_sample_count == 42
    assert status.ocr_confirmed_sample_count == 9
