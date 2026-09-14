"""Queue position: the fact that makes `queue_skip` automatable (заявка 13.09.2026 п.2).

The owner's own rule, confirmed 13.09.2026: the queue is the order tickets
were opened in, oldest first. That is data already on every `TicketSession`
(`created_at`), so a player's place in line is a pure computation over the
tickets currently open — no separate queue table, nothing that can drift
from what `TicketsCog` already tracks.

Pure: no I/O, no database, no Discord.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


class _QueuedTicket(Protocol):
    """The two fields queue position needs from a ticket session."""

    @property
    def channel_id(self) -> int: ...
    @property
    def author_id(self) -> int: ...
    @property
    def created_at(self) -> datetime: ...


@dataclass(frozen=True, slots=True)
class QueuePosition:
    """Where one ticket sits in line."""

    position: int
    """1-based — the ticket at the front of the queue is position 1."""
    total: int
    """How many tickets are in line at all."""

    @property
    def is_first(self) -> bool:
        """Whether this ticket is already at the front — nothing to skip to."""
        return self.position == 1


def order_queue[T: _QueuedTicket](tickets: Sequence[T]) -> list[T]:
    """Sort tickets into queue order: oldest `created_at` first.

    Args:
        tickets: The open tickets to order — callers decide what "open"
            means (e.g. excludes `CONFIRMED`); this function only sorts.
    """
    return sorted(tickets, key=lambda ticket: (ticket.created_at, ticket.channel_id))


def position_of[T: _QueuedTicket](tickets: Sequence[T], channel_id: int) -> QueuePosition | None:
    """Where one ticket sits among the given open tickets.

    Args:
        tickets: The open tickets to rank among (any order — this sorts).
        channel_id: The ticket to locate.

    Returns:
        `None` if `channel_id` is not among `tickets`.
    """
    ordered = order_queue(tickets)
    for index, ticket in enumerate(ordered, start=1):
        if ticket.channel_id == channel_id:
            return QueuePosition(position=index, total=len(ordered))
    return None


def move_to_front[T: _QueuedTicket](tickets: Sequence[T], channel_id: int) -> list[T]:
    """Return the queue with one ticket moved to the front — what `queue_skip` grants.

    Args:
        tickets: The open tickets, any order.
        channel_id: The ticket to move to the front.
    """
    ordered = order_queue(tickets)
    moved = [ticket for ticket in ordered if ticket.channel_id == channel_id]
    rest = [ticket for ticket in ordered if ticket.channel_id != channel_id]
    return moved + rest
