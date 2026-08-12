"""`HealthStatus` — the `/healthcheck` snapshot.

PLAN.md §12, M11; sqlite_migration.md §VIII, Э6.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class HealthStatus:
    """A point-in-time snapshot of the bot's operational health.

    Replaces the sheet-era Sheets/cache-sync counters (`sheets_read_requests`,
    `cache_hit_rate`, `last_users_sync`, ...) with SQLite's own state — there
    is no sync to report on anymore, only whether the database itself is
    intact and how much data it holds.
    """

    schema_version: int
    """`PRAGMA user_version` — the applied migration count."""
    player_count: int
    deal_count: int
    last_deal_at: datetime | None
    """Most recent `deals.occurred_at`, or `None` if the database has no deals yet."""
    db_size_bytes: int
    integrity_ok: bool
    """Result of `PRAGMA quick_check`, taken at snapshot time."""
    audit_queue_size: int
    ocr_sample_count: int
    ocr_confirmed_sample_count: int
