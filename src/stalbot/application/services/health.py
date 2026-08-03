"""Operational health snapshot: `/healthcheck` and the per-minute metrics log (PLAN.md §12, M11).

`HealthService.snapshot()` is the one place that pulls together everything
the two features need — bot-level state (uptime, connection status) is
intentionally *not* here, since it belongs to `StalbotBot`, not a testable
application service; the caller merges it in.
"""

from stalbot.application.dto.health_status import HealthStatus
from stalbot.application.ports.clock import Clock
from stalbot.application.services.audit import AuditService
from stalbot.infrastructure.cache.repositories.screenshot_analyses import (
    ScreenshotAnalysesRepository,
)
from stalbot.infrastructure.cache.repositories.users import UsersCacheRepository
from stalbot.infrastructure.cache.sync import CacheSync
from stalbot.infrastructure.sheets.client import SheetsClient


class HealthService:
    """Aggregates Sheets/cache/audit/OCR-dataset counters into one snapshot."""

    def __init__(
        self,
        sheets: SheetsClient,
        cache_sync: CacheSync,
        users: UsersCacheRepository,
        audit: AuditService,
        screenshots: ScreenshotAnalysesRepository,
        *,
        clock: Clock,
    ) -> None:
        """Wire the service to its collaborators.

        Args:
            sheets: For cumulative read/write request counts.
            cache_sync: For the hit rate and the last sync reports.
            users: For the cache's last-synced timestamp.
            audit: For the audit-delivery queue's current size.
            screenshots: For the OCR training-dataset counters.
            clock: Time source, tz-aware `GMT3`.
        """
        self._sheets = sheets
        self._cache_sync = cache_sync
        self._users = users
        self._audit = audit
        self._screenshots = screenshots
        self._clock = clock

    async def snapshot(self) -> HealthStatus:
        """Build a fresh `HealthStatus` from current counters and cache state."""
        last_synced = await self._users.last_synced_at()
        cache_age = (
            (self._clock.now() - last_synced).total_seconds() if last_synced is not None else None
        )
        return HealthStatus(
            sheets_read_requests=self._sheets.read_request_count,
            sheets_write_requests=self._sheets.write_request_count,
            cache_hit_rate=self._cache_sync.cache_hit_rate,
            cache_age_seconds=cache_age,
            last_users_sync=self._cache_sync.last_users_report,
            last_items_sync=self._cache_sync.last_items_report,
            audit_queue_size=self._audit.queue_size(),
            ocr_sample_count=await self._screenshots.count_all(),
            ocr_confirmed_sample_count=await self._screenshots.count_with_confirmed_amount(),
        )
