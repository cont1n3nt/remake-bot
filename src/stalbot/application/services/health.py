"""Operational health snapshot: `/healthcheck` and the per-minute metrics log (PLAN.md §12, M11).

`HealthService.snapshot()` is the one place that pulls together everything
the two features need — bot-level state (uptime, connection status) is
intentionally *not* here, since it belongs to `StalbotBot`, not a testable
application service; the caller merges it in.

sqlite_migration.md Э6: reports SQLite's own state (schema version, row
counts, integrity, file size) instead of Sheets/cache-sync counters — there
is no sync to report on anymore.
"""

from stalbot.application.dto.health_status import HealthStatus
from stalbot.application.services.audit import AuditService
from stalbot.infrastructure.cache.db import CacheDb
from stalbot.infrastructure.cache.repositories.deals import DealsRepository
from stalbot.infrastructure.cache.repositories.players import PlayersRepository
from stalbot.infrastructure.cache.repositories.screenshot_analyses import (
    ScreenshotAnalysesRepository,
)


class HealthService:
    """Aggregates database/audit/OCR-dataset counters into one snapshot."""

    def __init__(
        self,
        cache_db: CacheDb,
        players: PlayersRepository,
        deals: DealsRepository,
        audit: AuditService,
        screenshots: ScreenshotAnalysesRepository,
    ) -> None:
        """Wire the service to its collaborators.

        Args:
            cache_db: For schema version, integrity, and file size.
            players: For the player count.
            deals: For the deal count and most recent deal timestamp.
            audit: For the audit-delivery queue's current size.
            screenshots: For the OCR training-dataset counters.
        """
        self._cache_db = cache_db
        self._players = players
        self._deals = deals
        self._audit = audit
        self._screenshots = screenshots

    async def snapshot(self) -> HealthStatus:
        """Build a fresh `HealthStatus` from current database/audit/OCR state."""
        return HealthStatus(
            schema_version=await self._cache_db.schema_version(),
            player_count=await self._players.count(),
            deal_count=await self._deals.count(),
            last_deal_at=await self._deals.last_occurred_at(),
            db_size_bytes=self._cache_db.size_bytes(),
            integrity_ok=await self._cache_db.integrity_ok(),
            audit_queue_size=self._audit.queue_size(),
            ocr_sample_count=await self._screenshots.count_all(),
            ocr_confirmed_sample_count=await self._screenshots.count_with_confirmed_amount(),
        )
