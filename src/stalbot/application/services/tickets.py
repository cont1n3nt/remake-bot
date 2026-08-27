"""Ticket session lifecycle (PLAN.md §11.2-§11.5).

Tracks `TicketSession` state through the flow so persistent Views survive a
restart (§11.7). All Discord-specific rendering stays out of this service —
`render_ticket_card(session)` builds the card from whatever state is read
back here (invariant: the card is never assembled ad hoc in a handler).
"""

from dataclasses import replace
from datetime import datetime
from decimal import Decimal

from stalbot.application.dto.ticket_session import TicketSession
from stalbot.application.ports.clock import Clock
from stalbot.domain.entities.screenshot import OCR_STATUS_DISABLED
from stalbot.domain.enums import CouponKind, DeliveryMethod, TicketKind, TicketStatus
from stalbot.domain.errors import TicketSessionNotFoundError
from stalbot.infrastructure.cache.repositories.ticket_sessions import TicketSessionsRepository


class TicketService:
    """Mutates `ticket_sessions` state through the ticket flow."""

    def __init__(self, sessions: TicketSessionsRepository, *, clock: Clock) -> None:
        """Wire the service to its collaborators.

        Args:
            sessions: Cache repository for `ticket_sessions`.
            clock: Time source, tz-aware `GMT3`.
        """
        self._sessions = sessions
        self._clock = clock

    async def get(self, channel_id: int) -> TicketSession | None:
        """Return the session for a ticket channel, or `None` if untracked.

        Args:
            channel_id: Discord channel id.
        """
        return await self._sessions.get(channel_id)

    async def open_ticket(self, channel_id: int, kind: TicketKind, author_id: int) -> TicketSession:
        """Create the session for a newly-created ticket channel.

        Idempotent: if a session already exists for `channel_id` (e.g. a
        replayed gateway event), it is returned untouched rather than reset.

        Args:
            channel_id: The new ticket channel.
            kind: Which of the three tracked categories it belongs to.
            author_id: Best-effort guess at the ticket's opener; `0` if it
                could not be inferred from the channel's permission
                overwrites (the caller then fills it in once a real member
                interacts, via `set_author`).
        """
        existing = await self._sessions.get(channel_id)
        if existing is not None:
            return existing

        now = self._clock.now()
        session = TicketSession(
            channel_id=channel_id,
            kind=kind,
            author_id=author_id,
            status=TicketStatus.AWAITING_TOOL,
            delivery_method=None,
            game_nick=None,
            referrer_nick=None,
            referrer_discord_id=None,
            deadline=None,
            screenshot_url=None,
            screenshot_message_id=None,
            summary_message_id=None,
            panel_message_id=None,
            ocr_status=OCR_STATUS_DISABLED,
            ocr_analysis_id=None,
            idempotency_key=None,
            created_at=now,
            updated_at=now,
        )
        await self._sessions.upsert(session)
        return session

    async def set_author(self, channel_id: int, author_id: int) -> TicketSession:
        """Record the real ticket opener once they interact (PLAN.md §11.2).

        Args:
            channel_id: The ticket channel.
            author_id: The member who clicked "📝 Заполнить заявку".
        """
        return await self._update(channel_id, author_id=author_id)

    async def record_panel(self, channel_id: int, panel_message_id: int) -> TicketSession:
        """Record the id of the posted panel message.

        Args:
            channel_id: The ticket channel.
            panel_message_id: The panel message's id.
        """
        return await self._update(channel_id, panel_message_id=panel_message_id)

    async def record_delivery_method(
        self, channel_id: int, method: DeliveryMethod
    ) -> TicketSession:
        """Record the player's chosen delivery method and advance to `AWAITING_FORM`.

        Args:
            channel_id: The ticket channel.
            method: 📬 Почта or 🤝 Обмен.
        """
        return await self._update(
            channel_id, delivery_method=method, status=TicketStatus.AWAITING_FORM
        )

    async def record_form(
        self,
        channel_id: int,
        *,
        game_nick: str,
        referrer_nick: str | None,
        referrer_discord_id: int | None,
        deadline: datetime | None = None,
    ) -> TicketSession:
        """Record the submitted form and advance to `FILLED`.

        Args:
            channel_id: The ticket channel.
            game_nick: The player's in-game nick, as typed.
            referrer_nick: Referrer's in-game nick, if given.
            referrer_discord_id: Referrer's resolved Discord id, if the
                typed text matched a guild member.
            deadline: `ORDER_BOOSTS`' deadline field, already parsed and
                validated by `domain.clock.parse_deadline`; `None` for
                `SELL_ITEMS`/`SELL_BOOSTS`, which have no deadline field.
        """
        return await self._update(
            channel_id,
            game_nick=game_nick,
            referrer_nick=referrer_nick,
            referrer_discord_id=referrer_discord_id,
            deadline=deadline,
            status=TicketStatus.FILLED,
        )

    async def record_summary_message(self, channel_id: int, message_id: int) -> TicketSession:
        """Record the id of the public summary card message.

        Args:
            channel_id: The ticket channel.
            message_id: The summary card message's id.
        """
        return await self._update(channel_id, summary_message_id=message_id)

    async def record_screenshot(
        self, channel_id: int, image_url: str | None, message_id: int
    ) -> TicketSession:
        """Record the screenshot's permanent (log-channel) URL.

        Args:
            channel_id: The ticket channel.
            image_url: The log-channel message's CDN image URL (PLAN.md
                §11.5), or `None` if the log channel wasn't reachable.
            message_id: The screenshot message's id (in the ticket channel).
        """
        return await self._update(
            channel_id, screenshot_url=image_url, screenshot_message_id=message_id
        )

    async def record_confirmed(self, channel_id: int) -> TicketSession:
        """Mark the ticket as confirmed, once `TransactionService.register()` has run.

        Args:
            channel_id: The ticket channel.
        """
        return await self._update(channel_id, status=TicketStatus.CONFIRMED)

    async def record_coupon(
        self, channel_id: int, code: str, kind: CouponKind, discount_percent: Decimal
    ) -> TicketSession:
        """Lock a redeemed coupon's terms onto the session (заявка 26.08+27.08.2026).

        Args:
            channel_id: The ticket channel.
            code: The coupon's code, as stored (upper-cased).
            kind: Discount (заказ бустов) or markup (скупка).
            discount_percent: The `Decimal` percent to apply at confirm time.
        """
        return await self._update(
            channel_id, coupon_code=code, coupon_kind=kind, coupon_discount_percent=discount_percent
        )

    async def set_active_order_item(self, channel_id: int, item_id: int | None) -> TicketSession:
        """Record which boost-order line the editor's controls act on (PLAN.md §11.6).

        Args:
            channel_id: The ticket channel.
            item_id: Catalog id of the newly-selected line, or `None` to
                clear the selection (e.g. after that line is deleted).
        """
        return await self._update(channel_id, active_order_item_id=item_id)

    async def _update(self, channel_id: int, **changes: object) -> TicketSession:
        session = await self._sessions.get(channel_id)
        if session is None:
            raise TicketSessionNotFoundError(f"no tracked session for channel {channel_id}")
        updated = replace(session, updated_at=self._clock.now(), **changes)  # type: ignore[arg-type]
        await self._sessions.upsert(updated)
        return updated
