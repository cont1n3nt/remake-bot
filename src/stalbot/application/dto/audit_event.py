"""The audit log row DTO (see PLAN.md §5.4)."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """One row of the `🧾 Использование команды` audit embed."""

    user_id: int
    user_display: str
    channel_display: str
    command: str
    arguments: str
    result: str
    duration_seconds: float
    trace_id: str
    occurred_at: datetime
