"""Tests for `stalbot.application.services.tickets.TicketService` (PLAN.md §11.2-§11.5).

The cache repository is real, SQLite-backed, for genuine round-trip
confidence (same approach as `test_transaction_service.py`).
"""

from datetime import UTC, datetime

import aiosqlite
import pytest

from stalbot.application.services.tickets import TicketService
from stalbot.domain.enums import DeliveryMethod, TicketKind, TicketStatus
from stalbot.domain.errors import TicketSessionNotFoundError
from stalbot.infrastructure.cache.repositories.ticket_sessions import TicketSessionsRepository
from tests.support.fake_clock import FakeClock


def _service(connection: aiosqlite.Connection) -> TicketService:
    return TicketService(
        TicketSessionsRepository(connection),
        clock=FakeClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC)),
    )


async def test_open_ticket_creates_an_awaiting_tool_session(
    connection: aiosqlite.Connection,
) -> None:
    service = _service(connection)

    session = await service.open_ticket(111, TicketKind.SELL_ITEMS, 222)

    assert session.channel_id == 111
    assert session.kind is TicketKind.SELL_ITEMS
    assert session.author_id == 222
    assert session.status is TicketStatus.AWAITING_TOOL
    assert session.ocr_status == "disabled"


async def test_open_ticket_is_idempotent(connection: aiosqlite.Connection) -> None:
    service = _service(connection)
    first = await service.open_ticket(111, TicketKind.SELL_ITEMS, 222)

    second = await service.open_ticket(111, TicketKind.SELL_BOOSTS, 999)

    assert second == first


async def test_set_author_updates_the_author_id(connection: aiosqlite.Connection) -> None:
    service = _service(connection)
    await service.open_ticket(111, TicketKind.SELL_ITEMS, 0)

    updated = await service.set_author(111, 555)

    assert updated.author_id == 555


async def test_record_panel_stores_the_message_id(connection: aiosqlite.Connection) -> None:
    service = _service(connection)
    await service.open_ticket(111, TicketKind.SELL_ITEMS, 222)

    updated = await service.record_panel(111, 42)

    assert updated.panel_message_id == 42


async def test_record_delivery_method_advances_status(connection: aiosqlite.Connection) -> None:
    service = _service(connection)
    await service.open_ticket(111, TicketKind.SELL_ITEMS, 222)

    updated = await service.record_delivery_method(111, DeliveryMethod.MAIL)

    assert updated.delivery_method is DeliveryMethod.MAIL
    assert updated.status is TicketStatus.AWAITING_FORM


async def test_record_form_stores_fields_and_advances_status(
    connection: aiosqlite.Connection,
) -> None:
    service = _service(connection)
    await service.open_ticket(111, TicketKind.SELL_ITEMS, 222)

    updated = await service.record_form(
        111, game_nick="Scaryyyyy", referrer_nick="OtherNick", referrer_discord_id=999
    )

    assert updated.game_nick == "Scaryyyyy"
    assert updated.referrer_nick == "OtherNick"
    assert updated.referrer_discord_id == 999
    assert updated.status is TicketStatus.FILLED


async def test_record_form_stores_the_deadline_for_order_boosts(
    connection: aiosqlite.Connection,
) -> None:
    service = _service(connection)
    await service.open_ticket(111, TicketKind.ORDER_BOOSTS, 222)
    deadline = datetime(2026, 8, 2, 20, 0, tzinfo=UTC)

    updated = await service.record_form(
        111,
        game_nick="Scaryyyyy",
        referrer_nick=None,
        referrer_discord_id=None,
        deadline=deadline,
    )

    assert updated.deadline == deadline


async def test_set_active_order_item_stores_the_item_id(connection: aiosqlite.Connection) -> None:
    service = _service(connection)
    await service.open_ticket(111, TicketKind.ORDER_BOOSTS, 222)

    updated = await service.set_active_order_item(111, 42)

    assert updated.active_order_item_id == 42


async def test_set_active_order_item_clears_with_none(connection: aiosqlite.Connection) -> None:
    service = _service(connection)
    await service.open_ticket(111, TicketKind.ORDER_BOOSTS, 222)
    await service.set_active_order_item(111, 42)

    updated = await service.set_active_order_item(111, None)

    assert updated.active_order_item_id is None


async def test_record_summary_message_stores_the_message_id(
    connection: aiosqlite.Connection,
) -> None:
    service = _service(connection)
    await service.open_ticket(111, TicketKind.SELL_ITEMS, 222)

    updated = await service.record_summary_message(111, 77)

    assert updated.summary_message_id == 77


async def test_record_screenshot_stores_url_and_message_id(
    connection: aiosqlite.Connection,
) -> None:
    service = _service(connection)
    await service.open_ticket(111, TicketKind.SELL_ITEMS, 222)

    updated = await service.record_screenshot(111, "https://cdn/x.png", 88)

    assert updated.screenshot_url == "https://cdn/x.png"
    assert updated.screenshot_message_id == 88


async def test_record_confirmed_sets_the_status(connection: aiosqlite.Connection) -> None:
    service = _service(connection)
    await service.open_ticket(111, TicketKind.SELL_ITEMS, 222)

    updated = await service.record_confirmed(111)

    assert updated.status is TicketStatus.CONFIRMED


async def test_updates_on_an_untracked_channel_raise(connection: aiosqlite.Connection) -> None:
    service = _service(connection)

    with pytest.raises(TicketSessionNotFoundError):
        await service.record_panel(999, 1)


async def test_get_returns_none_for_untracked_channel(connection: aiosqlite.Connection) -> None:
    service = _service(connection)

    assert await service.get(999) is None
