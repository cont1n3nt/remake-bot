"""`TicketSession` — persistent ticket-flow state (PLAN.md §8.1, §11.7).

Has no Sheets counterpart. Ticket flow logic lands in M9/M10; for now this
is a thin, schema-shaped record so the cache repository has something
concrete to return. `status`/`delivery_method` stay plain `str` here — M9
will introduce the enums that constrain their values once the ticket flow
itself is designed.
"""

from dataclasses import dataclass
from datetime import datetime

from stalbot.domain.enums import TicketKind


@dataclass(frozen=True, slots=True)
class TicketSession:
    """One tracked ticket channel's state."""

    channel_id: int
    kind: TicketKind
    author_id: int
    status: str
    delivery_method: str | None
    game_nick: str | None
    referrer_nick: str | None
    referrer_discord_id: int | None
    deadline: datetime | None
    screenshot_url: str | None
    screenshot_message_id: int | None
    summary_message_id: int | None
    panel_message_id: int | None
    ocr_status: str
    ocr_analysis_id: int | None
    idempotency_key: str | None
    created_at: datetime
    updated_at: datetime
