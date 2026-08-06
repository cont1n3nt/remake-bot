"""TEST-7: persistent ticket Views must survive a bot restart.

`TicketPanelView`/`TicketSummaryView` only work across a restart if two
things both hold: their `custom_id`s are deterministic (so `bot.add_view()`
on a *brand-new* View instance still catches clicks on old messages — see
`presentation/cogs/tickets/views.py`'s module docstring), and the handler
those buttons call resolves ticket state from SQLite rather than any
in-memory dict on the old `TicketsCog`/`TicketService` objects (which are
gone after a restart).

Every other ticket test mocks `TicketService` out, which proves the cog
*calls* it correctly but can't prove state actually round-trips through a
process boundary. This test uses a real `CacheDb`/`TicketSessionsRepository`
and, critically, builds two entirely separate `TicketService`/`TicketsCog`
object graphs against the same on-disk SQLite file — one to simulate the
pre-restart bot, a second (sharing no Python object, only the file) to
simulate the post-restart bot — the same shape of discontinuity a real
process restart produces.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from stalbot.application.services.tickets import TicketService
from stalbot.config.ids import TICKET_CATEGORIES
from stalbot.domain.enums import TicketKind, TicketStatus
from stalbot.infrastructure.cache.db import CacheDb
from stalbot.infrastructure.cache.repositories.ticket_sessions import TicketSessionsRepository
from stalbot.presentation.cogs.tickets.cog import TicketsCog
from stalbot.presentation.cogs.tickets.modals import AmountModal
from stalbot.presentation.embeds.factory import EmbedFactory

_CHANNEL_ID = 111
_AUTHOR_ID = 222
_ADMIN_ID = 333

_SELL_ITEMS_CATEGORY = next(
    cid for cid, kind in TICKET_CATEGORIES.items() if kind is TicketKind.SELL_ITEMS
)


class _FixedClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


def _build_cog(sessions: TicketSessionsRepository) -> tuple[TicketsCog, TicketService]:
    """Build a full `TicketsCog` graph around a real ticket-sessions repository.

    The other four collaborators (`screenshots`/`boost_orders`/
    `transactions`/`progression`) play no part in the confirm-button path
    exercised here, so they stay mocked — only the persistence path this
    test is about needs to be real.
    """
    tickets = TicketService(sessions, clock=_FixedClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC)))
    cog = TicketsCog(
        tickets,
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        EmbedFactory(),
        MagicMock(log_channel_id=555),
    )
    return cog, tickets


def _admin_interaction() -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.channel_id = _CHANNEL_ID
    interaction.user = MagicMock(spec=discord.Member, id=_ADMIN_ID)
    interaction.user.guild_permissions = MagicMock(administrator=True)
    interaction.response = MagicMock()
    interaction.response.send_modal = AsyncMock()
    interaction.response.send_message = AsyncMock()
    return interaction


async def test_confirm_button_resolves_session_after_a_simulated_restart(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "cache.sqlite3"

    # --- "Pre-restart" bot: opens the ticket and fills out the form ---
    pre_restart_db = CacheDb(db_path)
    pre_restart_conn = await pre_restart_db.connect()
    pre_restart_cog, pre_restart_tickets = _build_cog(TicketSessionsRepository(pre_restart_conn))

    channel = MagicMock(spec=discord.TextChannel)
    channel.id = _CHANNEL_ID
    channel.category_id = _SELL_ITEMS_CATEGORY
    channel.overwrites = {}

    await pre_restart_tickets.open_ticket(_CHANNEL_ID, TicketKind.SELL_ITEMS, _AUTHOR_ID)
    await pre_restart_tickets.set_author(_CHANNEL_ID, _AUTHOR_ID)
    session = await pre_restart_tickets.record_form(
        _CHANNEL_ID,
        game_nick="Scaryyyyy",
        referrer_nick=None,
        referrer_discord_id=None,
    )
    assert session.status is TicketStatus.FILLED
    assert pre_restart_cog is not None  # built to prove the same wiring works pre-restart too

    # Simulate the process actually stopping: no shared Python object below
    # this line is reused by the "post-restart" side.
    await pre_restart_db.close()

    # --- "Post-restart" bot: brand-new object graph, same SQLite file ---
    post_restart_db = CacheDb(db_path)
    post_restart_conn = await post_restart_db.connect()
    post_restart_cog, _post_restart_tickets = _build_cog(
        TicketSessionsRepository(post_restart_conn)
    )

    # `bot.add_view()` would re-register these at startup; the custom_ids
    # must still match whatever is baked into the old message's components
    # for a stale button click to ever reach this cog at all.
    views: dict[str, Any] = {}
    for view in post_restart_cog.persistent_views():
        for item in view.children:
            if isinstance(item, discord.ui.Button) and item.custom_id:
                views[item.custom_id] = item
    assert "ticket:confirm" in views
    assert "ticket:screenshot" in views
    assert f"ticket:start:{TicketKind.SELL_ITEMS.value}" in views

    interaction = _admin_interaction()

    await post_restart_cog._on_confirm_button(interaction)

    # The only way this succeeds is if `_confirm_precheck` read `game_nick`
    # and `status` for channel 111 back out of SQLite — `post_restart_cog`
    # never saw `record_form`'s call, only the pre-restart instance did.
    interaction.response.send_modal.assert_awaited_once()
    modal = interaction.response.send_modal.call_args.args[0]
    assert isinstance(modal, AmountModal)

    await post_restart_db.close()
