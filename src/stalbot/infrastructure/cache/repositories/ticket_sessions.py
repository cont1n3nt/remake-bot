"""SQLite-backed `ticket_sessions` (PLAN.md §8.1, §11.7) — cache-only, no Sheets counterpart."""

from datetime import datetime

import aiosqlite

from stalbot.application.dto.ticket_session import TicketSession
from stalbot.domain.enums import DeliveryMethod, TicketKind, TicketStatus


class TicketSessionsRepository:
    """Persistent ticket-flow state, so persistent Views survive a restart (M9/M10)."""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        """Wrap an already-open cache connection.

        Args:
            connection: Connection returned by `CacheDb.connect()`.
        """
        self._conn = connection

    async def get(self, channel_id: int) -> TicketSession | None:
        """Return the session for a ticket channel, or `None` if untracked.

        Args:
            channel_id: Discord channel id.
        """
        cursor = await self._conn.execute(
            "SELECT * FROM ticket_sessions WHERE channel_id = ?", (channel_id,)
        )
        row = await cursor.fetchone()
        return _row_to_session(row) if row is not None else None

    async def upsert(self, session: TicketSession) -> None:
        """Insert or update a ticket session.

        Args:
            session: The session state to persist.
        """
        await self._conn.execute(
            """
            INSERT INTO ticket_sessions (
                channel_id, kind, author_id, status, delivery_method, game_nick,
                referrer_nick, referrer_discord_id, deadline, screenshot_url,
                screenshot_message_id, summary_message_id, panel_message_id,
                ocr_status, ocr_analysis_id, idempotency_key, created_at, updated_at,
                active_order_item_id
            ) VALUES (
                :channel_id, :kind, :author_id, :status, :delivery_method, :game_nick,
                :referrer_nick, :referrer_discord_id, :deadline, :screenshot_url,
                :screenshot_message_id, :summary_message_id, :panel_message_id,
                :ocr_status, :ocr_analysis_id, :idempotency_key, :created_at, :updated_at,
                :active_order_item_id
            )
            ON CONFLICT (channel_id) DO UPDATE SET
                kind = excluded.kind,
                author_id = excluded.author_id,
                status = excluded.status,
                delivery_method = excluded.delivery_method,
                game_nick = excluded.game_nick,
                referrer_nick = excluded.referrer_nick,
                referrer_discord_id = excluded.referrer_discord_id,
                deadline = excluded.deadline,
                screenshot_url = excluded.screenshot_url,
                screenshot_message_id = excluded.screenshot_message_id,
                summary_message_id = excluded.summary_message_id,
                panel_message_id = excluded.panel_message_id,
                ocr_status = excluded.ocr_status,
                ocr_analysis_id = excluded.ocr_analysis_id,
                idempotency_key = excluded.idempotency_key,
                updated_at = excluded.updated_at,
                active_order_item_id = excluded.active_order_item_id
            """,
            _session_to_params(session),
        )
        await self._conn.commit()

    async def delete(self, channel_id: int) -> None:
        """Remove a ticket session (channel closed/deleted).

        Args:
            channel_id: Discord channel id.
        """
        await self._conn.execute("DELETE FROM ticket_sessions WHERE channel_id = ?", (channel_id,))
        await self._conn.commit()

    async def all_open(self) -> list[TicketSession]:
        """Return every session, for restoring persistent Views at startup."""
        cursor = await self._conn.execute("SELECT * FROM ticket_sessions")
        return [_row_to_session(row) async for row in cursor]

    async def reassign_active_order_item(self, *, old_item_id: int, new_item_id: int) -> None:
        """Repoint sessions' selected order-editor line to a renumbered item id (APP-4).

        `/del_item` renumbers every surviving item's `id`; a session whose
        editor had a line from the shifted range selected (`active_order_item_id`)
        would otherwise keep pointing at the old number — the editor's `+`/
        `-`/delete buttons silently do nothing until the player reselects a
        line, since `BoostOrderService.compute_total`/renderers all key off
        the current id. Mirrors `BoostOrderLinesRepository.reassign_item_id`,
        which fixes the equivalent problem for draft order lines.

        Args:
            old_item_id: The id any pointing session currently has.
            new_item_id: The id the surviving item was renumbered to.
        """
        if old_item_id == new_item_id:
            return
        await self._conn.execute(
            "UPDATE ticket_sessions SET active_order_item_id = ?"
            " WHERE active_order_item_id = ?",
            (new_item_id, old_item_id),
        )
        await self._conn.commit()

    async def clear_active_order_item_for(self, item_id: int) -> None:
        """Clear the selected line for sessions pointing at a permanently deleted item (APP-4).

        Args:
            item_id: The deleted item's id, before renumbering.
        """
        await self._conn.execute(
            "UPDATE ticket_sessions SET active_order_item_id = NULL"
            " WHERE active_order_item_id = ?",
            (item_id,),
        )
        await self._conn.commit()


def _session_to_params(session: TicketSession) -> dict[str, object]:
    return {
        "channel_id": session.channel_id,
        "kind": session.kind.value,
        "author_id": session.author_id,
        "status": session.status.value,
        "delivery_method": session.delivery_method.value if session.delivery_method else None,
        "game_nick": session.game_nick,
        "referrer_nick": session.referrer_nick,
        "referrer_discord_id": session.referrer_discord_id,
        "deadline": session.deadline.isoformat() if session.deadline else None,
        "screenshot_url": session.screenshot_url,
        "screenshot_message_id": session.screenshot_message_id,
        "summary_message_id": session.summary_message_id,
        "panel_message_id": session.panel_message_id,
        "ocr_status": session.ocr_status,
        "ocr_analysis_id": session.ocr_analysis_id,
        "idempotency_key": session.idempotency_key,
        "created_at": session.created_at.isoformat(),
        "updated_at": session.updated_at.isoformat(),
        "active_order_item_id": session.active_order_item_id,
    }


def _row_to_session(row: aiosqlite.Row) -> TicketSession:
    return TicketSession(
        channel_id=row["channel_id"],
        kind=TicketKind(row["kind"]),
        author_id=row["author_id"],
        status=TicketStatus(row["status"]),
        delivery_method=DeliveryMethod(row["delivery_method"]) if row["delivery_method"] else None,
        game_nick=row["game_nick"],
        referrer_nick=row["referrer_nick"],
        referrer_discord_id=row["referrer_discord_id"],
        deadline=datetime.fromisoformat(row["deadline"]) if row["deadline"] else None,
        screenshot_url=row["screenshot_url"],
        screenshot_message_id=row["screenshot_message_id"],
        summary_message_id=row["summary_message_id"],
        panel_message_id=row["panel_message_id"],
        ocr_status=row["ocr_status"],
        ocr_analysis_id=row["ocr_analysis_id"],
        idempotency_key=row["idempotency_key"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        active_order_item_id=row["active_order_item_id"],
    )
