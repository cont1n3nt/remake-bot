"""Tests for `stalbot.application.services.health.HealthService` (PLAN.md §12, M11)."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from stalbot.application.services.audit import AuditService
from stalbot.application.services.health import HealthService
from stalbot.infrastructure.cache.repositories.screenshot_analyses import (
    ScreenshotAnalysesRepository,
)
from stalbot.infrastructure.cache.repositories.users import UsersCacheRepository
from stalbot.infrastructure.cache.sync import CacheSync, SyncReport
from stalbot.infrastructure.sheets.client import SheetsClient


class _FixedClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


def _sheets(*, reads: int = 5, writes: int = 2) -> MagicMock:
    client = MagicMock(spec=SheetsClient)
    client.read_request_count = reads
    client.write_request_count = writes
    return client


def _cache_sync(
    *, hit_rate: float | None, users_report: SyncReport | None, items_report: SyncReport | None
) -> MagicMock:
    cache_sync = MagicMock(spec=CacheSync)
    cache_sync.cache_hit_rate = hit_rate
    cache_sync.last_users_report = users_report
    cache_sync.last_items_report = items_report
    return cache_sync


def _users(*, last_synced_at: datetime | None) -> MagicMock:
    users = MagicMock(spec=UsersCacheRepository)
    users.last_synced_at = AsyncMock(return_value=last_synced_at)
    return users


def _audit(*, queue_size: int = 0) -> MagicMock:
    audit = MagicMock(spec=AuditService)
    audit.queue_size = MagicMock(return_value=queue_size)
    return audit


def _screenshots(*, total: int = 0, confirmed: int = 0) -> MagicMock:
    screenshots = MagicMock(spec=ScreenshotAnalysesRepository)
    screenshots.count_all = AsyncMock(return_value=total)
    screenshots.count_with_confirmed_amount = AsyncMock(return_value=confirmed)
    return screenshots


async def test_snapshot_reports_sheets_request_counts() -> None:
    service = HealthService(
        _sheets(reads=12, writes=3),
        _cache_sync(hit_rate=None, users_report=None, items_report=None),
        _users(last_synced_at=None),
        _audit(),
        _screenshots(),
        clock=_FixedClock(datetime(2026, 8, 3, 12, 0, tzinfo=UTC)),
    )

    status = await service.snapshot()

    assert status.sheets_read_requests == 12
    assert status.sheets_write_requests == 3


async def test_snapshot_computes_cache_age_from_last_synced_at() -> None:
    now = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    last_synced = datetime(2026, 8, 3, 11, 59, tzinfo=UTC)  # 60s ago
    service = HealthService(
        _sheets(),
        _cache_sync(hit_rate=0.9, users_report=None, items_report=None),
        _users(last_synced_at=last_synced),
        _audit(),
        _screenshots(),
        clock=_FixedClock(now),
    )

    status = await service.snapshot()

    assert status.cache_age_seconds == 60.0
    assert status.cache_hit_rate == 0.9


async def test_snapshot_reports_no_cache_age_when_never_synced() -> None:
    service = HealthService(
        _sheets(),
        _cache_sync(hit_rate=None, users_report=None, items_report=None),
        _users(last_synced_at=None),
        _audit(),
        _screenshots(),
        clock=_FixedClock(datetime(2026, 8, 3, 12, 0, tzinfo=UTC)),
    )

    status = await service.snapshot()

    assert status.cache_age_seconds is None


async def test_snapshot_passes_through_the_last_sync_reports() -> None:
    users_report = SyncReport(users_synced=10)
    items_report = SyncReport(items_synced=20)
    service = HealthService(
        _sheets(),
        _cache_sync(hit_rate=1.0, users_report=users_report, items_report=items_report),
        _users(last_synced_at=None),
        _audit(),
        _screenshots(),
        clock=_FixedClock(datetime(2026, 8, 3, 12, 0, tzinfo=UTC)),
    )

    status = await service.snapshot()

    assert status.last_users_sync is users_report
    assert status.last_items_sync is items_report


async def test_snapshot_reports_audit_queue_size_and_ocr_counters() -> None:
    service = HealthService(
        _sheets(),
        _cache_sync(hit_rate=None, users_report=None, items_report=None),
        _users(last_synced_at=None),
        _audit(queue_size=7),
        _screenshots(total=42, confirmed=9),
        clock=_FixedClock(datetime(2026, 8, 3, 12, 0, tzinfo=UTC)),
    )

    status = await service.snapshot()

    assert status.audit_queue_size == 7
    assert status.ocr_sample_count == 42
    assert status.ocr_confirmed_sample_count == 9
