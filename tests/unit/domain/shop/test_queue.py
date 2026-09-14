"""Tests for `domain.shop.queue` (заявка 13.09.2026 п.2).

The owner's own rule: the queue is the order tickets were opened in, oldest
first. Every test here is really testing that one sentence.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from stalbot.domain.shop.queue import move_to_front, order_queue, position_of

_T0 = datetime(2026, 9, 14, 10, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class _Ticket:
    channel_id: int
    author_id: int
    created_at: datetime


def _ticket(channel_id: int, minutes: int, author_id: int = 1) -> _Ticket:
    return _Ticket(
        channel_id=channel_id, author_id=author_id, created_at=_T0.replace(minute=minutes)
    )


def test_order_queue_sorts_oldest_first() -> None:
    late = _ticket(2, minutes=10)
    early = _ticket(1, minutes=0)

    assert order_queue([late, early]) == [early, late]


def test_order_queue_is_stable_for_equal_timestamps() -> None:
    """A tie-break is needed somewhere — channel id keeps it deterministic."""
    a = _ticket(1, minutes=5)
    b = _ticket(2, minutes=5)

    assert order_queue([b, a]) == [a, b]


def test_position_of_the_oldest_ticket_is_one() -> None:
    tickets = [_ticket(1, 0), _ticket(2, 5), _ticket(3, 10)]

    result = position_of(tickets, 1)

    assert result is not None
    assert result.position == 1
    assert result.total == 3
    assert result.is_first


def test_position_of_a_later_ticket() -> None:
    tickets = [_ticket(1, 0), _ticket(2, 5), _ticket(3, 10)]

    result = position_of(tickets, 3)

    assert result is not None
    assert result.position == 3
    assert not result.is_first


def test_position_of_an_untracked_ticket_is_none() -> None:
    tickets = [_ticket(1, 0)]

    assert position_of(tickets, 999) is None


def test_move_to_front_puts_the_chosen_ticket_first() -> None:
    tickets = [_ticket(1, 0), _ticket(2, 5), _ticket(3, 10)]

    moved = move_to_front(tickets, 3)

    assert [t.channel_id for t in moved] == [3, 1, 2]


def test_move_to_front_keeps_the_rest_in_queue_order() -> None:
    tickets = [_ticket(3, 10), _ticket(1, 0), _ticket(2, 5)]

    moved = move_to_front(tickets, 3)

    assert [t.channel_id for t in moved] == [3, 1, 2]


def test_a_ticket_already_first_stays_put() -> None:
    tickets = [_ticket(1, 0), _ticket(2, 5)]

    moved = move_to_front(tickets, 1)

    assert [t.channel_id for t in moved] == [1, 2]
