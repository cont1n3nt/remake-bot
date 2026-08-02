"""Tests for `TicketSessionsRepository` against a real (temp-file) SQLite connection."""

from datetime import UTC, datetime

import aiosqlite

from stalbot.application.dto.ticket_session import TicketSession
from stalbot.domain.enums import TicketKind
from stalbot.infrastructure.cache.repositories.ticket_sessions import TicketSessionsRepository


def _session(channel_id: int = 111, **overrides: object) -> TicketSession:
    now = datetime(2026, 7, 31, 21, 45, tzinfo=UTC)
    defaults: dict[str, object] = {
        "channel_id": channel_id,
        "kind": TicketKind.SELL_ITEMS,
        "author_id": 222,
        "status": "awaiting_tool",
        "delivery_method": None,
        "game_nick": None,
        "referrer_nick": None,
        "referrer_discord_id": None,
        "deadline": None,
        "screenshot_url": None,
        "screenshot_message_id": None,
        "summary_message_id": None,
        "panel_message_id": None,
        "ocr_status": "disabled",
        "ocr_analysis_id": None,
        "idempotency_key": None,
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(overrides)
    return TicketSession(**defaults)  # type: ignore[arg-type]


async def test_get_returns_none_when_untracked(connection: aiosqlite.Connection) -> None:
    repo = TicketSessionsRepository(connection)
    assert await repo.get(999) is None


async def test_upsert_then_get_round_trips(connection: aiosqlite.Connection) -> None:
    repo = TicketSessionsRepository(connection)
    session = _session(game_nick="Scaryyyyy", ocr_status="pending")

    await repo.upsert(session)
    result = await repo.get(111)

    assert result == session


async def test_upsert_overwrites_existing_session(connection: aiosqlite.Connection) -> None:
    repo = TicketSessionsRepository(connection)
    await repo.upsert(_session(status="awaiting_tool"))
    await repo.upsert(_session(status="confirmed"))

    result = await repo.get(111)

    assert result is not None
    assert result.status == "confirmed"


async def test_delete_removes_the_session(connection: aiosqlite.Connection) -> None:
    repo = TicketSessionsRepository(connection)
    await repo.upsert(_session())

    await repo.delete(111)

    assert await repo.get(111) is None


async def test_all_open_lists_every_session(connection: aiosqlite.Connection) -> None:
    repo = TicketSessionsRepository(connection)
    await repo.upsert(_session(channel_id=1))
    await repo.upsert(_session(channel_id=2))

    sessions = await repo.all_open()

    assert {s.channel_id for s in sessions} == {1, 2}


async def test_deadline_round_trips(connection: aiosqlite.Connection) -> None:
    repo = TicketSessionsRepository(connection)
    deadline = datetime(2026, 8, 2, 20, 0, tzinfo=UTC)
    await repo.upsert(_session(deadline=deadline, kind=TicketKind.ORDER_BOOSTS))

    result = await repo.get(111)

    assert result is not None
    assert result.deadline == deadline
    assert result.kind is TicketKind.ORDER_BOOSTS
