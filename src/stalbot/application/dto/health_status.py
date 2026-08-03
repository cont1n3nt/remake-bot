"""`HealthStatus` — the `/healthcheck` snapshot (PLAN.md §12, M11)."""

from dataclasses import dataclass

from stalbot.infrastructure.cache.sync import SyncReport


@dataclass(frozen=True, slots=True)
class HealthStatus:
    """A point-in-time snapshot of the bot's operational health."""

    sheets_read_requests: int
    sheets_write_requests: int
    cache_hit_rate: float | None
    """Fraction of reads that found the cache already fresh, `None` if unknown yet."""
    cache_age_seconds: float | None
    """Age of the most recent successful users/transactions sync, `None` if never synced."""
    last_users_sync: SyncReport | None
    last_items_sync: SyncReport | None
    audit_queue_size: int
    ocr_sample_count: int
    ocr_confirmed_sample_count: int
