"""`TicketSession` — persistent ticket-flow state (PLAN.md §8.1, §11.7).

Has no Sheets counterpart. `boost_order_lines` (M10) holds the actual
selected boosts/quantities per channel; `active_order_item_id` here is just
a pointer into that list — which line the editor's quantity/delete controls
currently act on (PLAN.md §11.6).
"""

from dataclasses import dataclass
from datetime import datetime

from stalbot.domain.enums import DeliveryMethod, TicketKind, TicketStatus


@dataclass(frozen=True, slots=True)
class TicketSession:
    """One tracked ticket channel's state."""

    channel_id: int
    kind: TicketKind
    author_id: int
    status: TicketStatus
    delivery_method: DeliveryMethod | None
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
    active_order_item_id: int | None = None
