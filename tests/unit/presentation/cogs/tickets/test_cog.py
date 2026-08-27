"""Tests for `stalbot.presentation.cogs.tickets.cog.TicketsCog` (PLAN.md §11).

Services are mocked; Discord objects are `MagicMock(spec=...)`. Listener
methods (`on_guild_channel_create`, `on_message`) are plain coroutines
under the `@commands.Cog.listener()` decorator, so they're called directly
— no `.callback` indirection needed (that's only for `app_commands.Command`).
"""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from stalbot.application.dto.boost_order_line import BoostOrderLine
from stalbot.application.dto.ticket_session import TicketSession
from stalbot.application.dto.transaction_request import TransactionRegistrationResult
from stalbot.config.ids import TICKET_CATEGORIES, TICKET_TOOL_BOT_ID
from stalbot.domain.entities.coupon import Coupon
from stalbot.domain.entities.deal import Deal
from stalbot.domain.entities.player import Player
from stalbot.domain.entities.screenshot import OcrResult
from stalbot.domain.enums import (
    CouponKind,
    DealSource,
    DealType,
    DeliveryMethod,
    ItemCategory,
    OccurredAtKind,
    TicketKind,
    TicketStatus,
)
from stalbot.domain.errors import AmountParseError
from stalbot.domain.progression.ranks import RankLadder
from stalbot.presentation.cogs.tickets.cog import TicketsCog, _infer_author_id, _resolve_member
from stalbot.presentation.cogs.tickets.modals import (
    AmountModal,
    CouponModal,
    OrderBoostsFormModal,
    TicketFormModal,
)
from stalbot.presentation.cogs.tickets.order_views import OrderEditorView, OrderSummaryView
from stalbot.presentation.embeds.factory import EmbedFactory
from tests.support.fake_clock import FakeClock

_SELL_ITEMS_CATEGORY = next(
    cid for cid, kind in TICKET_CATEGORIES.items() if kind is TicketKind.SELL_ITEMS
)
_ORDER_BOOSTS_CATEGORY = next(
    cid for cid, kind in TICKET_CATEGORIES.items() if kind is TicketKind.ORDER_BOOSTS
)


def _session(**overrides: object) -> TicketSession:
    now = datetime(2026, 7, 31, 21, 45, tzinfo=UTC)
    defaults: dict[str, object] = {
        "channel_id": 111,
        "kind": TicketKind.SELL_ITEMS,
        "author_id": 222,
        "status": TicketStatus.AWAITING_TOOL,
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
        "active_order_item_id": None,
    }
    defaults.update(overrides)
    return TicketSession(**defaults)  # type: ignore[arg-type]


def _player(**overrides: object) -> Player:
    now = datetime(2026, 7, 31, 21, 45, tzinfo=UTC)
    defaults: dict[str, object] = {
        "id": 1,
        "nick_norm": "scaryyyyy",
        "nick_display": "Scaryyyyy",
        "discord_id": None,
        "referrer_player_id": None,
        "is_booster": False,
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(overrides)
    return Player(**defaults)  # type: ignore[arg-type]


def _coupon(**overrides: object) -> Coupon:
    now = datetime(2026, 8, 26, tzinfo=UTC)
    defaults: dict[str, object] = {
        "id": 1,
        "code": "KLONDIKE10",
        "kind": CouponKind.DISCOUNT,
        "discount_percent": Decimal("1.5"),
        "max_uses": None,
        "used_count": 0,
        "active": True,
        "created_by": 1,
        "created_at": now,
        "expires_at": None,
    }
    defaults.update(overrides)
    return Coupon(**defaults)  # type: ignore[arg-type]


def _fake_tickets(*, get_return: TicketSession | None = None) -> MagicMock:
    tickets = MagicMock()
    tickets.open_ticket = AsyncMock(return_value=get_return or _session())
    tickets.get = AsyncMock(return_value=get_return)
    tickets.set_author = AsyncMock(return_value=get_return)
    tickets.record_panel = AsyncMock(return_value=get_return)
    tickets.record_delivery_method = AsyncMock(return_value=get_return)
    tickets.record_form = AsyncMock(return_value=get_return or _session())
    tickets.record_summary_message = AsyncMock(return_value=get_return)
    tickets.record_screenshot = AsyncMock(return_value=get_return or _session())
    tickets.record_confirmed = AsyncMock(return_value=get_return)
    tickets.set_active_order_item = AsyncMock(return_value=get_return)
    tickets.record_coupon = AsyncMock(return_value=get_return)
    return tickets


def _fake_boost_orders() -> MagicMock:
    boost_orders = MagicMock()
    boost_orders.list_available_items = AsyncMock(return_value=[])
    boost_orders.list_lines = AsyncMock(return_value=[])
    boost_orders.list_lines_with_items = AsyncMock(return_value=[])
    boost_orders.apply_page_selection = AsyncMock(return_value=frozenset())
    boost_orders.set_quantity = AsyncMock()
    boost_orders.adjust_quantity = AsyncMock(return_value=1)
    boost_orders.remove_line = AsyncMock()
    boost_orders.compute_total = AsyncMock(return_value=Decimal(0))
    boost_orders.compute_order_total = AsyncMock(return_value=Decimal(0))
    boost_orders.clear = AsyncMock()
    return boost_orders


def _fake_screenshots() -> MagicMock:
    screenshots = MagicMock()
    screenshots.on_attached = AsyncMock()
    screenshots.record_confirmed_amount = AsyncMock()
    return screenshots


def _fake_transactions(**overrides: object) -> MagicMock:
    deal_defaults: dict[str, object] = {
        "id": 1,
        "player_id": 1,
        "occurred_at": datetime(2026, 8, 2, 12, 0, tzinfo=UTC),
        "occurred_at_kind": OccurredAtKind.BOT,
        "deal_type": DealType.SALE,
        "amount": Decimal(100000),
        "coins": 1,
        "xp": 10,
        "rank_at_deal": None,
        "booster_at_deal": False,
        "recorded_by": None,
        "source": DealSource.TICKET,
        "legacy_sheet_row": None,
        "created_at": datetime(2026, 8, 2, 12, 0, tzinfo=UTC),
    }
    result = TransactionRegistrationResult(
        deal=Deal(**deal_defaults),  # type: ignore[arg-type]
        nick_display="Scaryyyyy",
        discord_bound=False,
    )
    transactions = MagicMock()
    transactions.register = AsyncMock(return_value=overrides.get("register_result", result))
    return transactions


def _fake_progression() -> MagicMock:
    progression = MagicMock()
    progression.sync = AsyncMock(return_value=[])
    return progression


def _fake_players() -> MagicMock:
    players = MagicMock()
    # No existing player by default — `_lock_referrer` is a no-op unless a
    # test opts in with `get_by_nick`/`get_by_id` return values of its own.
    players.get_by_nick = AsyncMock(return_value=None)
    players.get_by_id = AsyncMock(return_value=None)
    return players


def _fake_coupons() -> MagicMock:
    coupons = MagicMock()
    coupons.redeem = AsyncMock()
    return coupons


def _cog(
    *,
    tickets: MagicMock | None = None,
    screenshots: MagicMock | None = None,
    boost_orders: MagicMock | None = None,
    transactions: MagicMock | None = None,
    progression: MagicMock | None = None,
    players: MagicMock | None = None,
    coupons: MagicMock | None = None,
    embeds: EmbedFactory | None = None,
    tool_wait_timeout: float = 0.05,
    log_channel_id: int = 555,
) -> tuple[TicketsCog, MagicMock, MagicMock, MagicMock, MagicMock, MagicMock]:
    tickets = tickets or _fake_tickets()
    screenshots = screenshots or _fake_screenshots()
    boost_orders = boost_orders or _fake_boost_orders()
    transactions = transactions or _fake_transactions()
    progression = progression or _fake_progression()
    players = players or _fake_players()
    coupons = coupons or _fake_coupons()
    settings = MagicMock(log_channel_id=log_channel_id)
    cog = TicketsCog(
        tickets,
        screenshots,
        boost_orders,
        transactions,
        progression,
        players,
        coupons,
        embeds or EmbedFactory(),
        settings,
        tool_wait_timeout_seconds=tool_wait_timeout,
    )
    return cog, tickets, screenshots, boost_orders, transactions, progression


def _text_channel(
    *, channel_id: int = 111, category_id: int | None = _SELL_ITEMS_CATEGORY, overwrites: Any = None
) -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    channel.category_id = category_id
    channel.overwrites = overwrites or {}
    # `None` by default (like `_interaction()`'s `guild`) — role pricing
    # resolution treats a missing guild as "no multiplier", so tests that
    # don't care about §9.1's role-based pricing don't need to stub it.
    channel.guild = None
    sent_message = MagicMock(spec=discord.Message)
    sent_message.id = 999
    sent_message.embeds = []
    channel.send = AsyncMock(return_value=sent_message)
    channel.fetch_message = AsyncMock(return_value=None)
    return channel


def _message(
    *,
    author_id: int,
    channel: MagicMock | None = None,
    is_bot: bool = False,
    attachments: list[Any] | None = None,
) -> MagicMock:
    message = MagicMock(spec=discord.Message)
    message.author = MagicMock(id=author_id, bot=is_bot)
    message.channel = channel or _text_channel()
    message.attachments = attachments or []
    return message


def _interaction(
    *, channel: MagicMock | None = None, user_id: int = 42, guild: MagicMock | None = None
) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.channel = channel or _text_channel()
    interaction.channel_id = interaction.channel.id
    interaction.guild = guild
    interaction.user = MagicMock(spec=discord.Member, id=user_id, display_name="Scaryyyyy")
    interaction.user.guild_permissions = MagicMock(administrator=False)
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.send_modal = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.response.edit_message = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


# -- Channel lifecycle --------------------------------------------------


async def test_on_guild_channel_create_ignores_untracked_categories() -> None:
    cog, tickets, *_ = _cog()
    channel = _text_channel(category_id=999999)

    await cog.on_guild_channel_create(channel)

    tickets.open_ticket.assert_not_called()


async def test_on_guild_channel_create_handles_order_boosts_too() -> None:
    cog, tickets, *_ = _cog(tool_wait_timeout=0.02)
    channel = _text_channel(category_id=_ORDER_BOOSTS_CATEGORY)

    await cog.on_guild_channel_create(channel)

    tickets.open_ticket.assert_awaited_once()
    (_channel_id, kind, _author_id), _ = tickets.open_ticket.call_args
    assert kind is TicketKind.ORDER_BOOSTS


async def test_on_guild_channel_create_posts_the_panel_after_the_timeout() -> None:
    cog, tickets, *_ = _cog(tool_wait_timeout=0.02)
    channel = _text_channel()

    await cog.on_guild_channel_create(channel)

    tickets.open_ticket.assert_awaited_once()
    channel.send.assert_awaited_once()
    tickets.record_panel.assert_awaited_once()


async def test_on_guild_channel_create_posts_the_panel_as_soon_as_tool_speaks() -> None:
    cog, tickets, *_ = _cog(tool_wait_timeout=5.0)
    channel = _text_channel()

    task = asyncio.create_task(cog.on_guild_channel_create(channel))
    await asyncio.sleep(0)
    await cog.on_message(_message(author_id=TICKET_TOOL_BOT_ID, channel=channel))
    await asyncio.wait_for(task, timeout=1.0)

    channel.send.assert_awaited_once()
    tickets.open_ticket.assert_awaited_once()


async def test_on_guild_channel_create_survives_the_channel_disappearing() -> None:
    """TICK-6: the channel can be deleted before the panel is posted — must not raise."""
    cog, tickets, *_ = _cog(tool_wait_timeout=0.02)
    channel = _text_channel()
    channel.send = AsyncMock(side_effect=discord.NotFound(MagicMock(status=404, reason=""), "gone"))

    await cog.on_guild_channel_create(channel)  # must not raise

    tickets.record_panel.assert_not_called()


async def test_on_message_ignores_bot_authors_that_are_not_ticket_tool() -> None:
    cog, tickets, *_ = _cog()

    await cog.on_message(_message(author_id=1, is_bot=True, attachments=[MagicMock()]))

    tickets.get.assert_not_called()


async def test_on_message_ignores_messages_without_attachments_in_a_tracked_channel() -> None:
    cog, tickets, *_ = _cog(tickets=_fake_tickets(get_return=_session()))

    await cog.on_message(_message(author_id=42))

    tickets.get.assert_not_called()


async def test_on_message_ignores_an_image_when_the_button_was_not_pressed() -> None:
    """UX #15: an unrequested image in the ticket channel must not be recorded."""
    cog, tickets, *_ = _cog(tickets=_fake_tickets(get_return=_session()))
    attachment = MagicMock(spec=discord.Attachment, size=1024, content_type="image/png")

    await cog.on_message(_message(author_id=42, attachments=[attachment]))

    tickets.get.assert_not_called()


async def test_on_message_handles_an_image_once_the_button_was_pressed() -> None:
    """UX #15: the gate opens after `_on_screenshot_button`, for that channel only."""
    session = _session()
    cog, tickets, *_ = _cog(tickets=_fake_tickets(get_return=session))
    channel = _text_channel()
    interaction = _interaction(channel=channel)
    await cog._on_screenshot_button(interaction)

    message = _message(author_id=42, channel=channel)
    message.guild = None
    attachment = MagicMock(spec=discord.Attachment, size=1024, content_type="image/png")
    attachment.read = AsyncMock(return_value=b"fake-bytes")
    message.attachments = [attachment]

    await cog.on_message(message)

    tickets.get.assert_awaited_once_with(channel.id)


# -- Form flow ------------------------------------------------------------


async def test_on_start_does_not_touch_the_author() -> None:
    """TICK-2: the first click of the shared panel button no longer decides authorship."""
    cog, tickets, *_ = _cog(tickets=_fake_tickets(get_return=_session(author_id=0)))
    interaction = _interaction(user_id=777)

    await cog._on_start(interaction, TicketKind.SELL_ITEMS)

    tickets.set_author.assert_not_called()
    interaction.response.send_message.assert_awaited_once()
    kwargs = interaction.response.send_message.call_args.kwargs
    assert kwargs["ephemeral"] is True


async def test_on_delivery_selected_records_and_opens_the_form() -> None:
    session = _session(kind=TicketKind.SELL_ITEMS)
    cog, tickets, *_ = _cog(tickets=_fake_tickets(get_return=session))
    interaction = _interaction()

    await cog._on_delivery_selected(interaction, DeliveryMethod.MAIL)

    tickets.record_delivery_method.assert_awaited_once_with(
        interaction.channel_id, DeliveryMethod.MAIL
    )
    interaction.response.send_modal.assert_awaited_once()


async def test_on_delivery_selected_opens_the_order_form_for_order_boosts() -> None:
    """ORDER_BOOSTS also picks a delivery method first now (PLAN.md §11.6)."""
    session = _session(kind=TicketKind.ORDER_BOOSTS)
    cog, tickets, *_ = _cog(tickets=_fake_tickets(get_return=session))
    interaction = _interaction()

    await cog._on_delivery_selected(interaction, DeliveryMethod.TRADE)

    tickets.record_delivery_method.assert_awaited_once_with(
        interaction.channel_id, DeliveryMethod.TRADE
    )
    modal = interaction.response.send_modal.call_args.args[0]
    assert isinstance(modal, OrderBoostsFormModal)


async def test_on_form_submitted_posts_a_new_summary_card() -> None:
    session = _session(game_nick="Scaryyyyy")
    cog, tickets, *_ = _cog(tickets=_fake_tickets(get_return=session))
    channel = _text_channel()
    interaction = _interaction(channel=channel)

    await cog._on_form_submitted(interaction, "Scaryyyyy", None, None)

    tickets.record_form.assert_awaited_once()
    channel.send.assert_awaited_once()
    tickets.record_summary_message.assert_awaited_once()
    interaction.followup.send.assert_awaited_once()


async def test_on_form_submitted_resolves_a_mentioned_referrer() -> None:
    cog, tickets, *_ = _cog()
    referrer_member = MagicMock(spec=discord.Member, id=888)
    guild = MagicMock(spec=discord.Guild)
    guild.members = [referrer_member]
    guild.get_member = MagicMock(return_value=referrer_member)
    interaction = _interaction(guild=guild)

    await cog._on_form_submitted(interaction, "Scaryyyyy", "OtherNick", "<@888>")

    _args, kwargs = tickets.record_form.call_args
    assert kwargs["referrer_discord_id"] == 888


async def test_on_form_submitted_sets_the_author_to_whoever_submitted_it() -> None:
    """TICK-2: authorship is decided at form submission, not at the button click."""
    cog, tickets, *_ = _cog()
    interaction = _interaction(user_id=777)

    await cog._on_form_submitted(interaction, "Scaryyyyy", None, None)

    tickets.set_author.assert_awaited_once_with(interaction.channel_id, 777)


async def test_on_form_submitted_drops_a_case_insensitive_self_referral() -> None:
    """TICK-8: a referral matching the submitter's own (normalized) nick is discarded."""
    cog, tickets, *_ = _cog()
    interaction = _interaction()

    await cog._on_form_submitted(interaction, "Scaryyyyy", "  scaryyyyy ", "<@888>")

    _args, kwargs = tickets.record_form.call_args
    assert kwargs["referrer_nick"] is None
    assert kwargs["referrer_discord_id"] is None


async def test_on_form_submitted_reopens_the_modal_when_only_one_referrer_field_is_filled() -> None:
    """заявка 21.08.2026 п.7: both referrer fields or neither."""
    cog, tickets, *_ = _cog()
    interaction = _interaction()

    await cog._on_form_submitted(interaction, "Scaryyyyy", "OtherNick", None)

    interaction.response.send_modal.assert_awaited_once()
    modal = interaction.response.send_modal.call_args.args[0]
    assert isinstance(modal, TicketFormModal)
    tickets.record_form.assert_not_called()


async def test_on_form_submitted_locks_an_existing_referrer() -> None:
    """заявка 21.08.2026 п.8: a player can only set their referrer once."""
    existing_referrer = _player(id=5, nick_display="FirstReferrer")
    player = _player(id=1, nick_norm="scaryyyyy", referrer_player_id=5)
    players = _fake_players()
    players.get_by_nick = AsyncMock(return_value=player)
    players.get_by_id = AsyncMock(return_value=existing_referrer)
    cog, tickets, *_ = _cog(players=players)
    interaction = _interaction()

    await cog._on_form_submitted(interaction, "Scaryyyyy", "SomeoneElse", "<@888>")

    _args, kwargs = tickets.record_form.call_args
    assert kwargs["referrer_nick"] is None
    assert kwargs["referrer_discord_id"] is None
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "FirstReferrer" in (embed.description or "")


async def test_on_form_submitted_allows_retyping_the_same_locked_referrer() -> None:
    existing_referrer = _player(id=5, nick_norm="firstreferrer", nick_display="FirstReferrer")
    player = _player(id=1, nick_norm="scaryyyyy", referrer_player_id=5)
    players = _fake_players()
    players.get_by_nick = AsyncMock(return_value=player)
    players.get_by_id = AsyncMock(return_value=existing_referrer)
    cog, tickets, *_ = _cog(players=players)
    interaction = _interaction()

    await cog._on_form_submitted(interaction, "Scaryyyyy", "FirstReferrer", "<@888>")

    _args, kwargs = tickets.record_form.call_args
    assert kwargs["referrer_nick"] == "FirstReferrer"


# -- Confirmation -----------------------------------------------------------


async def test_confirm_button_rejects_non_admins() -> None:
    cog, tickets, *_ = _cog()
    interaction = _interaction()

    await cog._on_confirm_button(interaction)

    tickets.get.assert_not_called()
    interaction.response.send_message.assert_awaited_once()
    embed = interaction.response.send_message.call_args.kwargs["embed"]
    assert "Недостаточно прав" in (embed.description or "")


async def test_confirm_button_rejects_an_unfilled_ticket() -> None:
    cog, _tickets, *_ = _cog(tickets=_fake_tickets(get_return=_session(game_nick=None)))
    interaction = _interaction()
    interaction.user.guild_permissions.administrator = True

    await cog._on_confirm_button(interaction)

    embed = interaction.response.send_message.call_args.kwargs["embed"]
    assert "не заполнена" in (embed.description or "")


async def test_confirm_button_rejects_an_already_confirmed_ticket() -> None:
    session = _session(game_nick="Scaryyyyy", status=TicketStatus.CONFIRMED)
    cog, _tickets, *_ = _cog(tickets=_fake_tickets(get_return=session))
    interaction = _interaction()
    interaction.user.guild_permissions.administrator = True

    await cog._on_confirm_button(interaction)

    embed = interaction.response.send_message.call_args.kwargs["embed"]
    assert "уже была подтверждена" in (embed.description or "")


async def test_confirm_button_opens_the_amount_modal_for_a_filled_ticket() -> None:
    session = _session(game_nick="Scaryyyyy")
    cog, _tickets, *_ = _cog(tickets=_fake_tickets(get_return=session))
    interaction = _interaction()
    interaction.user.guild_permissions.administrator = True

    await cog._on_confirm_button(interaction)

    interaction.response.send_modal.assert_awaited_once()


async def test_amount_modal_error_reaches_the_admin_end_to_end() -> None:
    """SEC-4 integration: `_on_amount_submitted` calls `evaluate_amount(...)` with no
    try/except of its own — before this fix, a bad amount would raise past `on_submit`
    into discord.py's default `Modal.on_error`, which just logs and returns, leaving
    the admin staring at a hung "thinking..." indicator. Exercises the real
    `AmountModal` the cog actually constructs (via `_on_confirm_button`), not a stub,
    through `on_submit` raising and `on_error` handling it — the same two steps
    discord.py's own dispatcher performs in production.
    """
    session = _session(game_nick="Scaryyyyy")
    cog, *_ = _cog(tickets=_fake_tickets(get_return=session))
    open_interaction = _interaction()
    open_interaction.user.guild_permissions.administrator = True

    await cog._on_confirm_button(open_interaction)
    modal = open_interaction.response.send_modal.call_args.args[0]
    assert isinstance(modal, AmountModal)

    modal.amount._value = "not an amount"
    submit_interaction = _interaction()
    submit_interaction.response.is_done = MagicMock(return_value=True)  # already deferred

    with pytest.raises(AmountParseError) as excinfo:
        await modal.on_submit(submit_interaction)

    await modal.on_error(submit_interaction, excinfo.value)

    submit_interaction.followup.send.assert_awaited_once()
    embed = submit_interaction.followup.send.call_args.kwargs["embed"]
    assert "распознать сумму" in (embed.description or "")


async def test_amount_submitted_rejects_a_ticket_confirmed_since_the_modal_was_opened() -> None:
    """TICK-1: a double confirm (double-click, two admins) must not double-register.

    `_confirm_precheck` only ran when the modal was opened; by the time the
    modal is submitted, a concurrent submission may have already confirmed
    the ticket. Re-checking here avoids re-running every post-confirm side
    effect for a deal that is already recorded.
    """
    session = _session(game_nick="Scaryyyyy", status=TicketStatus.CONFIRMED)
    cog, tickets, screenshots, _boost_orders, transactions, progression = _cog(
        tickets=_fake_tickets(get_return=session)
    )
    interaction = _interaction()

    await cog._on_amount_submitted(interaction, "100 000")

    transactions.register.assert_not_called()
    tickets.record_confirmed.assert_not_called()
    screenshots.record_confirmed_amount.assert_not_called()
    progression.sync.assert_not_called()
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "уже была подтверждена" in (embed.description or "")


async def test_amount_submitted_skips_duplicate_side_effects_on_a_replayed_registration() -> None:
    """TICK-1/TICK-11: a truly simultaneous double-submit both pass the pre-register status
    check (neither has confirmed yet), but only one of them actually writes — the other gets
    `TransactionService.register()`'s idempotent replay back and must not re-run announcements.
    """
    session = _session(game_nick="Scaryyyyy")
    replayed_deal = Deal(
        id=1,
        player_id=1,
        occurred_at=datetime(2026, 8, 2, 12, 0, tzinfo=UTC),
        occurred_at_kind=OccurredAtKind.BOT,
        deal_type=DealType.SALE,
        amount=Decimal(100000),  # type: ignore[arg-type]
        coins=1,
        xp=10,
        rank_at_deal=None,
        booster_at_deal=False,
        recorded_by=None,
        source=DealSource.TICKET,
        legacy_sheet_row=None,
        created_at=datetime(2026, 8, 2, 12, 0, tzinfo=UTC),
    )
    replayed_result = TransactionRegistrationResult(
        deal=replayed_deal, nick_display="Scaryyyyy", discord_bound=False, replayed=True
    )
    cog, tickets, screenshots, boost_orders, _transactions, progression = _cog(
        tickets=_fake_tickets(get_return=session),
        transactions=_fake_transactions(register_result=replayed_result),
    )
    channel = _text_channel()
    interaction = _interaction(channel=channel)

    await cog._on_amount_submitted(interaction, "100 000")

    tickets.record_confirmed.assert_awaited_once_with(session.channel_id)
    screenshots.record_confirmed_amount.assert_not_called()
    boost_orders.clear.assert_not_called()
    progression.sync.assert_not_called()
    channel.send.assert_not_called()
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "уже зафиксирована" in (embed.title or "")


async def test_amount_submitted_registers_the_deal_and_confirms() -> None:
    session = _session(game_nick="Scaryyyyy", referrer_nick="OtherNick")
    cog, tickets, screenshots, _boost_orders, transactions, progression = _cog(
        tickets=_fake_tickets(get_return=session)
    )
    channel = _text_channel()
    interaction = _interaction(channel=channel)

    await cog._on_amount_submitted(interaction, "100 000")

    transactions.register.assert_awaited_once()
    (request,), _ = transactions.register.call_args
    assert request.nick == "Scaryyyyy"
    assert request.deal_type is DealType.PURCHASE
    assert request.discord_id == session.author_id
    tickets.record_confirmed.assert_awaited_once_with(session.channel_id)
    screenshots.record_confirmed_amount.assert_awaited_once_with(
        session.channel_id, Decimal(100000)
    )
    progression.sync.assert_awaited_once()
    interaction.followup.send.assert_awaited_once()
    channel.send.assert_awaited_once()


async def test_amount_submitted_tags_the_author_and_asks_for_a_review() -> None:
    """заявка 21.08.2026 п.4: the public confirmation tags the author and points at reviews."""
    session = _session(game_nick="Scaryyyyy", author_id=42)
    cog, *_ = _cog(tickets=_fake_tickets(get_return=session))
    channel = _text_channel()
    interaction = _interaction(channel=channel)

    await cog._on_amount_submitted(interaction, "100 000")

    kwargs = channel.send.call_args.kwargs
    assert kwargs["content"] == "<@42>"
    assert "отзыв" in (kwargs["embed"].description or "")


async def test_amount_submitted_applies_the_coupon_discount_to_the_registered_amount() -> None:
    """заявка 26.08.2026: a redeemed coupon discounts the actual recorded deal amount."""
    session = _session(
        game_nick="Scaryyyyy",
        coupon_code="KLONDIKE10",
        coupon_kind=CouponKind.DISCOUNT,
        coupon_discount_percent=Decimal("10"),
    )
    cog, _tickets, _screenshots, _boost_orders, transactions, _progression = _cog(
        tickets=_fake_tickets(get_return=session)
    )
    interaction = _interaction()

    await cog._on_amount_submitted(interaction, "100000")

    (request,), _kwargs = transactions.register.call_args
    assert request.amount == Decimal(90000)
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "KLONDIKE10" in (embed.description or "")


# -- Coupons (заявка 26.08.2026) ------------------------------------------


async def test_coupon_button_opens_the_code_modal() -> None:
    cog, *_ = _cog()
    interaction = _interaction()

    await cog._on_coupon_button(interaction)

    interaction.response.send_modal.assert_awaited_once()
    modal = interaction.response.send_modal.call_args.args[0]
    assert isinstance(modal, CouponModal)


async def test_coupon_submitted_redeems_records_and_reports() -> None:
    session = _session(kind=TicketKind.SELL_ITEMS)
    coupons = _fake_coupons()
    coupons.redeem = AsyncMock(
        return_value=_coupon(
            code="KLONDIKE10", kind=CouponKind.MARKUP, discount_percent=Decimal("1.5")
        )
    )
    cog, tickets, *_ = _cog(tickets=_fake_tickets(get_return=session), coupons=coupons)
    channel = _text_channel()
    interaction = _interaction(channel=channel, user_id=777)

    await cog._on_coupon_submitted(interaction, "klondike10")

    coupons.redeem.assert_awaited_once_with(
        "klondike10",
        channel_id=interaction.channel_id,
        discord_id=777,
        ticket_kind=TicketKind.SELL_ITEMS,
    )
    tickets.record_coupon.assert_awaited_once_with(
        interaction.channel_id, "KLONDIKE10", CouponKind.MARKUP, Decimal("1.5")
    )
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "KLONDIKE10" in (embed.description or "")
    channel.send.assert_awaited_once()  # re-posts the ticket card with the coupon line on it


async def test_coupon_submitted_rejects_an_already_confirmed_ticket() -> None:
    session = _session(status=TicketStatus.CONFIRMED)
    coupons = _fake_coupons()
    cog, _tickets, *_ = _cog(tickets=_fake_tickets(get_return=session), coupons=coupons)
    interaction = _interaction()

    await cog._on_coupon_submitted(interaction, "KLONDIKE10")

    coupons.redeem.assert_not_called()
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "уже была подтверждена" in (embed.description or "")


# -- Screenshot ---------------------------------------------------------


async def test_screenshot_button_shows_the_requirements() -> None:
    cog, *_ = _cog()
    interaction = _interaction()

    await cog._on_screenshot_button(interaction)

    interaction.response.send_message.assert_awaited_once()
    embed = interaction.response.send_message.call_args.kwargs["embed"]
    assert "8 МБ" in (embed.description or "")
    assert interaction.channel_id in cog._awaiting_screenshot


async def test_handle_screenshots_rejects_oversized_files() -> None:
    cog, tickets, screenshots, *_ = _cog()
    channel = _text_channel()
    message = _message(author_id=1, channel=channel)
    attachment = MagicMock(spec=discord.Attachment, size=9 * 1024 * 1024, content_type="image/png")

    await cog._handle_screenshots(message, _session(), [attachment])

    channel.send.assert_awaited_once()
    embed = channel.send.call_args.kwargs["embed"]
    assert "слишком большой" in (embed.title or "")
    tickets.record_screenshot.assert_not_called()
    screenshots.on_attached.assert_not_called()
    message.delete.assert_not_awaited()


async def test_handle_screenshots_archives_and_updates_the_card() -> None:
    session = _session(summary_message_id=321)
    cog, tickets, screenshots, *_ = _cog(tickets=_fake_tickets(get_return=session))
    log_channel = _text_channel(channel_id=555)
    guild = MagicMock(spec=discord.Guild)
    guild.get_channel = MagicMock(return_value=log_channel)
    ticket_channel = _text_channel()
    ticket_channel.guild = guild
    ticket_channel.fetch_message = AsyncMock(
        return_value=MagicMock(spec=discord.Message, edit=AsyncMock())
    )
    message = _message(author_id=1, channel=ticket_channel)
    message.guild = guild
    message.attachments = []
    attachment = MagicMock(spec=discord.Attachment, size=1024, content_type="image/png")
    attachment.read = AsyncMock(return_value=b"fake-bytes")
    cog._awaiting_screenshot.add(session.channel_id)

    await cog._handle_screenshots(message, session, [attachment])

    log_channel.send.assert_awaited_once()
    tickets.record_screenshot.assert_awaited_once()
    screenshots.on_attached.assert_awaited_once()
    ticket_channel.fetch_message.assert_awaited_once_with(321)

    # UX #2: the raw upload is removed from the channel once it's archived.
    message.delete.assert_awaited_once()

    # UX #12: a transient "closest to ephemeral outside an interaction"
    # confirmation, addressed to the uploader, self-deletes.
    ticket_channel.send.assert_awaited_once()
    confirm_kwargs = ticket_channel.send.call_args.kwargs
    assert confirm_kwargs["content"] == "<@1>"
    assert confirm_kwargs["delete_after"] == 8.0
    assert "закреплён" in (confirm_kwargs["embed"].description or "")

    # The gate closes again once a screenshot has actually been recorded.
    assert session.channel_id not in cog._awaiting_screenshot


async def test_handle_screenshots_still_confirms_when_deleting_the_upload_fails() -> None:
    """A missing-permissions/already-gone message must not stop the confirmation."""
    session = _session(summary_message_id=None)
    cog, tickets, _screenshots, *_ = _cog(tickets=_fake_tickets(get_return=session))
    log_channel = _text_channel(channel_id=555)
    guild = MagicMock(spec=discord.Guild)
    guild.get_channel = MagicMock(return_value=log_channel)
    ticket_channel = _text_channel()
    ticket_channel.guild = guild
    message = _message(author_id=1, channel=ticket_channel)
    message.guild = guild
    message.attachments = []
    message.delete = AsyncMock(
        side_effect=discord.HTTPException(MagicMock(status=403), "Missing Permissions")
    )
    attachment = MagicMock(spec=discord.Attachment, size=1024, content_type="image/png")
    attachment.read = AsyncMock(return_value=b"fake-bytes")

    await cog._handle_screenshots(message, session, [attachment])

    message.delete.assert_awaited_once()
    ticket_channel.send.assert_awaited_once()
    tickets.record_screenshot.assert_awaited_once()


async def test_handle_screenshots_archives_every_attachment_but_cards_only_the_first() -> None:
    """UX #13: all images in the message are archived/analyzed, not just the first."""
    session = _session(summary_message_id=None)
    cog, tickets, screenshots, *_ = _cog(tickets=_fake_tickets(get_return=session))
    log_channel = _text_channel(channel_id=555)
    guild = MagicMock(spec=discord.Guild)
    guild.get_channel = MagicMock(return_value=log_channel)
    ticket_channel = _text_channel()
    ticket_channel.guild = guild
    message = _message(author_id=1, channel=ticket_channel)
    message.guild = guild
    message.attachments = []

    def _attachment() -> MagicMock:
        attachment = MagicMock(spec=discord.Attachment, size=1024, content_type="image/png")
        attachment.read = AsyncMock(return_value=b"fake-bytes")
        return attachment

    attachments = [_attachment(), _attachment(), _attachment()]

    await cog._handle_screenshots(message, session, attachments)

    assert log_channel.send.await_count == 3
    assert screenshots.on_attached.await_count == 3
    # Only the cover image is recorded on the session.
    tickets.record_screenshot.assert_awaited_once()
    confirm_kwargs = ticket_channel.send.call_args.kwargs
    assert "3 шт." in (confirm_kwargs["embed"].description or "")


async def test_handle_screenshot_is_byte_identical_regardless_of_the_ocr_outcome() -> None:
    """TEST-6: decision A7 says OCR must never affect the ticket flow — v1.0
    hardcodes `NullOcrGateway`, but that alone doesn't prove `_handle_screenshot`
    stays indifferent to whatever `ScreenshotService.on_attached()` returns;
    it just means today's only OCR outcome happens to be `"disabled"`. This
    drives the same screenshot through three very different outcomes (the
    real v1.0 `"disabled"` result, a hypothetical M13 `"done"` result with
    recognized items, and a raised exception — `on_attached` itself already
    swallows OCR errors per APP-8, but a *caller*-side bug could still let
    one through) and asserts every effect `_handle_screenshot` produces
    before/independent of that call — the card edit and the archive upload —
    comes out byte-for-byte the same every time, and that the discarded
    return value never reaches a branch.

    Uses a fixed-clock `EmbedFactory` rather than the real one `_cog()`
    defaults to: `render_ticket_card`'s footer is timestamped at minute
    precision, so a wall-clock minute rollover between loop iterations
    would otherwise make the embed snapshots differ for a reason that has
    nothing to do with OCR — a false red unrelated to the contract this
    test actually guards.
    """
    embeds = EmbedFactory(clock=FakeClock(datetime(2026, 8, 2, 12, 0, tzinfo=UTC)))
    outcomes: list[AsyncMock] = [
        AsyncMock(return_value=OcrResult(status="disabled")),
        AsyncMock(
            return_value=OcrResult(
                status="done",
                items=(),
                total_estimate="123.45",
                confidence=0.97,
            )
        ),
        AsyncMock(side_effect=RuntimeError("a caller-side bug let this through")),
    ]

    def _call_snapshot(call: Any) -> dict[str, Any]:
        # `discord.Embed`/`discord.File` are distinct objects on every call
        # (no value equality) even when built from identical data — compare
        # their actual content instead of object identity.
        kwargs = call.kwargs
        return {
            "embed": kwargs["embed"].to_dict(),
            "attachment_filenames": [f.filename for f in kwargs.get("attachments", [])]
            or ([kwargs["file"].filename] if "file" in kwargs else []),
        }

    edit_calls: list[dict[str, Any]] = []
    archive_calls: list[dict[str, Any]] = []
    record_screenshot_calls: list[Any] = []

    for on_attached in outcomes:
        session = _session(summary_message_id=321)
        cog, tickets, screenshots, *_ = _cog(
            tickets=_fake_tickets(get_return=session), embeds=embeds
        )
        screenshots.on_attached = on_attached

        log_channel = _text_channel(channel_id=555)
        guild = MagicMock(spec=discord.Guild)
        guild.get_channel = MagicMock(return_value=log_channel)
        ticket_channel = _text_channel()
        ticket_channel.guild = guild
        summary_message = MagicMock(spec=discord.Message, edit=AsyncMock())
        ticket_channel.fetch_message = AsyncMock(return_value=summary_message)
        message = _message(author_id=1, channel=ticket_channel)
        message.id = 777
        message.guild = guild
        message.attachments = []
        attachment = MagicMock(spec=discord.Attachment, size=1024, content_type="image/png")
        attachment.read = AsyncMock(return_value=b"fake-bytes")

        # `_handle_screenshots` now isolates `on_attached` behind its own
        # try/except — a raising outcome (the third one) must never escape,
        # and everything that runs after the archive/card update in the
        # happy path (gate close, delete, confirmation) must still happen.
        await cog._handle_screenshots(message, session, [attachment])
        message.delete.assert_awaited_once()
        ticket_channel.send.assert_awaited_once()  # the confirmation message
        assert session.channel_id not in cog._awaiting_screenshot

        edit_calls.append(_call_snapshot(summary_message.edit.call_args))
        archive_calls.append(_call_snapshot(log_channel.send.call_args))
        record_screenshot_calls.append(tickets.record_screenshot.call_args)

    assert edit_calls[0] == edit_calls[1] == edit_calls[2]
    assert archive_calls[0] == archive_calls[1] == archive_calls[2]
    assert record_screenshot_calls[0] == record_screenshot_calls[1] == record_screenshot_calls[2]


# -- Order-boosts form & editor --------------------------------------------


async def _call_on_start_order_boosts(cog: TicketsCog, interaction: MagicMock) -> None:
    await cog._on_start(interaction, TicketKind.ORDER_BOOSTS)


async def test_on_start_shows_the_delivery_picker_for_order_boosts_too() -> None:
    """ORDER_BOOSTS now asks for a delivery method first, same as every other kind."""
    cog, *_ = _cog(tickets=_fake_tickets(get_return=_session(kind=TicketKind.ORDER_BOOSTS)))
    interaction = _interaction()

    await _call_on_start_order_boosts(cog, interaction)

    interaction.response.send_message.assert_awaited_once()
    kwargs = interaction.response.send_message.call_args.kwargs
    assert kwargs["ephemeral"] is True
    interaction.response.send_modal.assert_not_called()


async def test_order_form_submitted_records_the_deadline_and_posts_the_summary() -> None:
    """UX #1: the order starts on the read-only summary, not the interactive editor."""
    session = _session(kind=TicketKind.ORDER_BOOSTS, game_nick="Scaryyyyy")
    cog, tickets, *_ = _cog(tickets=_fake_tickets(get_return=session))
    channel = _text_channel()
    interaction = _interaction(channel=channel)

    await cog._on_order_form_submitted(interaction, "Scaryyyyy", "через 3 часа", None, None)

    tickets.record_form.assert_awaited_once()
    _args, kwargs = tickets.record_form.call_args
    assert kwargs["deadline"] is not None
    channel.send.assert_awaited_once()
    assert isinstance(channel.send.call_args.kwargs["view"], OrderSummaryView)
    tickets.record_summary_message.assert_awaited_once()


async def test_order_form_submitted_reopens_the_modal_on_a_bad_deadline() -> None:
    cog, tickets, *_ = _cog()
    interaction = _interaction()

    await cog._on_order_form_submitted(interaction, "Scaryyyyy", "not a date", "Ref", None)

    tickets.record_form.assert_not_called()
    tickets.set_author.assert_not_called()
    interaction.response.send_modal.assert_awaited_once()


async def test_order_form_submitted_sets_the_author_to_whoever_submitted_it() -> None:
    """TICK-2, same as `_on_form_submitted`."""
    session = _session(kind=TicketKind.ORDER_BOOSTS, game_nick="Scaryyyyy")
    cog, tickets, *_ = _cog(tickets=_fake_tickets(get_return=session))
    interaction = _interaction(user_id=777)

    await cog._on_order_form_submitted(interaction, "Scaryyyyy", "через 3 часа", None, None)

    tickets.set_author.assert_awaited_once_with(interaction.channel_id, 777)


async def test_order_form_submitted_drops_a_case_insensitive_self_referral() -> None:
    """TICK-8, same as `_on_form_submitted`."""
    session = _session(kind=TicketKind.ORDER_BOOSTS, game_nick="Scaryyyyy")
    cog, tickets, *_ = _cog(tickets=_fake_tickets(get_return=session))
    interaction = _interaction()

    await cog._on_order_form_submitted(
        interaction, "Scaryyyyy", "через 3 часа", "  scaryyyyy ", "<@888>"
    )

    _args, kwargs = tickets.record_form.call_args
    assert kwargs["referrer_nick"] is None
    assert kwargs["referrer_discord_id"] is None


async def test_order_form_submitted_reopens_the_modal_for_a_half_filled_referrer() -> None:
    """заявка 21.08.2026 п.7, same as `_on_form_submitted`."""
    cog, tickets, *_ = _cog()
    interaction = _interaction()

    await cog._on_order_form_submitted(interaction, "Scaryyyyy", "через 3 часа", None, "<@888>")

    interaction.response.send_modal.assert_awaited_once()
    modal = interaction.response.send_modal.call_args.args[0]
    assert isinstance(modal, OrderBoostsFormModal)
    tickets.record_form.assert_not_called()


async def test_order_form_submitted_locks_an_existing_referrer() -> None:
    """заявка 21.08.2026 п.8, same as `_on_form_submitted`."""
    existing_referrer = _player(id=5, nick_display="FirstReferrer")
    player = _player(id=1, nick_norm="scaryyyyy", referrer_player_id=5)
    players = _fake_players()
    players.get_by_nick = AsyncMock(return_value=player)
    players.get_by_id = AsyncMock(return_value=existing_referrer)
    session = _session(kind=TicketKind.ORDER_BOOSTS, game_nick="Scaryyyyy")
    cog, tickets, *_ = _cog(tickets=_fake_tickets(get_return=session), players=players)
    interaction = _interaction()

    await cog._on_order_form_submitted(
        interaction, "Scaryyyyy", "через 3 часа", "SomeoneElse", "<@888>"
    )

    _args, kwargs = tickets.record_form.call_args
    assert kwargs["referrer_nick"] is None
    assert kwargs["referrer_discord_id"] is None


async def test_order_line_selected_stores_the_active_item_and_edits_in_place() -> None:
    session = _session(kind=TicketKind.ORDER_BOOSTS, author_id=42)
    cog, tickets, *_ = _cog(tickets=_fake_tickets(get_return=session))
    interaction = _interaction()

    await cog._on_order_line_selected(interaction, 42)

    tickets.set_active_order_item.assert_awaited_once_with(session.channel_id, 42)
    interaction.response.edit_message.assert_awaited_once()


async def test_order_line_selected_rejects_a_non_participant() -> None:
    session = _session(kind=TicketKind.ORDER_BOOSTS, author_id=999)
    cog, tickets, *_ = _cog(tickets=_fake_tickets(get_return=session))
    interaction = _interaction(user_id=42)

    await cog._on_order_line_selected(interaction, 42)

    tickets.set_active_order_item.assert_not_called()
    embed = interaction.response.send_message.call_args.kwargs["embed"]
    assert "Недостаточно прав" in (embed.description or "")


async def test_order_line_selected_rejects_an_already_confirmed_ticket() -> None:
    """TICK-3: every editor mutator must reject once the ticket is confirmed, not just confirm."""
    session = _session(kind=TicketKind.ORDER_BOOSTS, author_id=42, status=TicketStatus.CONFIRMED)
    cog, tickets, *_ = _cog(tickets=_fake_tickets(get_return=session))
    interaction = _interaction()

    await cog._on_order_line_selected(interaction, 42)

    tickets.set_active_order_item.assert_not_called()
    embed = interaction.response.send_message.call_args.kwargs["embed"]
    assert "уже была подтверждена" in (embed.description or "")


async def test_adjust_quantity_warns_when_nothing_is_selected() -> None:
    session = _session(kind=TicketKind.ORDER_BOOSTS, author_id=42, active_order_item_id=None)
    cog, _tickets, _screenshots, boost_orders, *_ = _cog(tickets=_fake_tickets(get_return=session))
    interaction = _interaction()

    await cog._on_order_qty_plus(interaction)

    boost_orders.adjust_quantity.assert_not_called()
    embed = interaction.response.send_message.call_args.kwargs["embed"]
    assert "Ничего не выбрано" in (embed.title or "")


async def test_adjust_quantity_delegates_to_the_service() -> None:
    session = _session(kind=TicketKind.ORDER_BOOSTS, author_id=42, active_order_item_id=7)
    cog, _tickets, _screenshots, boost_orders, *_ = _cog(tickets=_fake_tickets(get_return=session))
    interaction = _interaction()

    await cog._on_order_qty_plus(interaction)

    boost_orders.adjust_quantity.assert_awaited_once_with(session.channel_id, 7, 1)
    interaction.response.edit_message.assert_awaited_once()


async def test_qty_input_opens_the_modal_when_a_line_is_selected() -> None:
    session = _session(kind=TicketKind.ORDER_BOOSTS, author_id=42, active_order_item_id=7)
    cog, *_ = _cog(tickets=_fake_tickets(get_return=session))
    interaction = _interaction()

    await cog._on_order_qty_input(interaction)

    interaction.response.send_modal.assert_awaited_once()


async def test_qty_submitted_rejects_an_unparseable_amount() -> None:
    session = _session(kind=TicketKind.ORDER_BOOSTS, author_id=42, active_order_item_id=7)
    cog, _tickets, _screenshots, boost_orders, *_ = _cog(tickets=_fake_tickets(get_return=session))
    interaction = _interaction()

    await cog._on_order_qty_submitted(interaction, "not a number")

    boost_orders.set_quantity.assert_not_called()
    embed = interaction.response.send_message.call_args.kwargs["embed"]
    assert "количество" in (embed.description or "")


async def test_qty_submitted_rejects_an_out_of_range_amount() -> None:
    session = _session(kind=TicketKind.ORDER_BOOSTS, author_id=42, active_order_item_id=7)
    cog, _tickets, _screenshots, boost_orders, *_ = _cog(tickets=_fake_tickets(get_return=session))
    interaction = _interaction()

    await cog._on_order_qty_submitted(interaction, "10000")

    boost_orders.set_quantity.assert_not_called()


async def test_qty_submitted_rejects_an_already_confirmed_ticket() -> None:
    """TICK-3: the modal-submit path re-fetches its own session, so it needs its own check."""
    session = _session(
        kind=TicketKind.ORDER_BOOSTS,
        author_id=42,
        active_order_item_id=7,
        status=TicketStatus.CONFIRMED,
    )
    cog, _tickets, _screenshots, boost_orders, *_ = _cog(tickets=_fake_tickets(get_return=session))
    interaction = _interaction()

    await cog._on_order_qty_submitted(interaction, "5")

    boost_orders.set_quantity.assert_not_called()
    embed = interaction.response.send_message.call_args.kwargs["embed"]
    assert "уже была подтверждена" in (embed.description or "")


async def test_qty_submitted_rejects_a_fractional_amount() -> None:
    """TICK-10: "9999.9" must be rejected, not silently truncated to 9999."""
    session = _session(kind=TicketKind.ORDER_BOOSTS, author_id=42, active_order_item_id=7)
    cog, _tickets, _screenshots, boost_orders, *_ = _cog(tickets=_fake_tickets(get_return=session))
    interaction = _interaction()

    await cog._on_order_qty_submitted(interaction, "9999.9")

    boost_orders.set_quantity.assert_not_called()
    embed = interaction.response.send_message.call_args.kwargs["embed"]
    assert "целым числом" in (embed.description or "")


async def test_qty_submitted_rejects_a_non_participant() -> None:
    session = _session(kind=TicketKind.ORDER_BOOSTS, author_id=999, active_order_item_id=7)
    cog, _tickets, _screenshots, boost_orders, *_ = _cog(tickets=_fake_tickets(get_return=session))
    interaction = _interaction(user_id=42)

    await cog._on_order_qty_submitted(interaction, "5")

    boost_orders.set_quantity.assert_not_called()
    embed = interaction.response.send_message.call_args.kwargs["embed"]
    assert "Недостаточно прав" in (embed.description or "")


async def test_qty_submitted_sets_the_quantity_and_updates_the_editor() -> None:
    session = _session(kind=TicketKind.ORDER_BOOSTS, author_id=42, active_order_item_id=7)
    channel = _text_channel()
    cog, _tickets, _screenshots, boost_orders, *_ = _cog(tickets=_fake_tickets(get_return=session))
    interaction = _interaction(channel=channel)

    await cog._on_order_qty_submitted(interaction, "5")

    boost_orders.set_quantity.assert_awaited_once_with(session.channel_id, 7, 5)
    channel.send.assert_awaited_once()


async def test_delete_line_removes_and_clears_the_active_selection() -> None:
    session = _session(kind=TicketKind.ORDER_BOOSTS, author_id=42, active_order_item_id=7)
    cog, tickets, _screenshots, boost_orders, *_ = _cog(tickets=_fake_tickets(get_return=session))
    interaction = _interaction()

    await cog._on_order_delete_line(interaction)

    boost_orders.remove_line.assert_awaited_once_with(session.channel_id, 7)
    tickets.set_active_order_item.assert_awaited_once_with(session.channel_id, None)


async def test_add_boosts_opens_the_multiselect_picker() -> None:
    session = _session(kind=TicketKind.ORDER_BOOSTS, author_id=42)
    cog, *_ = _cog(tickets=_fake_tickets(get_return=session))
    interaction = _interaction()

    await cog._on_order_add_boosts(interaction)

    interaction.response.send_message.assert_awaited_once()
    kwargs = interaction.response.send_message.call_args.kwargs
    assert kwargs["ephemeral"] is True


async def test_add_boosts_passes_existing_quantities_to_the_picker() -> None:
    session = _session(kind=TicketKind.ORDER_BOOSTS, author_id=42)
    boost_orders = _fake_boost_orders()
    boost_orders.list_lines = AsyncMock(
        return_value=[
            BoostOrderLine(
                channel_id=111,
                item_id=1,
                item_name_norm="топот",
                category=ItemCategory.BOOST,
                quantity=3,
            )
        ]
    )
    cog, *_ = _cog(tickets=_fake_tickets(get_return=session), boost_orders=boost_orders)
    interaction = _interaction()

    await cog._on_order_add_boosts(interaction)

    view = interaction.response.send_message.call_args.kwargs["view"]
    assert view._quantities == {1: 3}


async def test_add_boosts_rejects_a_non_participant() -> None:
    session = _session(kind=TicketKind.ORDER_BOOSTS, author_id=999)
    cog, *_ = _cog(tickets=_fake_tickets(get_return=session))
    interaction = _interaction(user_id=42)

    await cog._on_order_add_boosts(interaction)

    embed = interaction.response.send_message.call_args.kwargs["embed"]
    assert "Недостаточно прав" in (embed.description or "")


async def test_add_boosts_allows_an_admin_who_is_not_the_author() -> None:
    session = _session(kind=TicketKind.ORDER_BOOSTS, author_id=999)
    cog, *_ = _cog(tickets=_fake_tickets(get_return=session))
    interaction = _interaction(user_id=42)
    interaction.user.guild_permissions.administrator = True

    await cog._on_order_add_boosts(interaction)

    kwargs = interaction.response.send_message.call_args.kwargs
    assert kwargs["ephemeral"] is True


async def test_order_boosts_changed_applies_the_page_and_updates_the_editor() -> None:
    session = _session(kind=TicketKind.ORDER_BOOSTS)
    channel = _text_channel()
    cog, _tickets, _screenshots, boost_orders, *_ = _cog(tickets=_fake_tickets(get_return=session))
    interaction = _interaction(channel=channel)

    await cog._on_order_boosts_changed(interaction, [], frozenset({1}))

    boost_orders.apply_page_selection.assert_awaited_once_with(
        session.channel_id, [], frozenset({1})
    )
    channel.send.assert_awaited_once()


async def test_order_confirm_rejects_an_empty_order() -> None:
    session = _session(kind=TicketKind.ORDER_BOOSTS, game_nick="Scaryyyyy")
    cog, *_ = _cog(tickets=_fake_tickets(get_return=session))
    interaction = _interaction()
    interaction.user.guild_permissions.administrator = True

    await cog._on_order_confirm(interaction)

    embed = interaction.response.send_message.call_args.kwargs["embed"]
    assert "нет ни одной позиции" in (embed.description or "")


async def test_order_confirm_returns_to_the_read_only_summary() -> None:
    """UX #1: the editor's "✅ Подтвердить" no longer opens the amount modal directly —
    it switches the message back to the read-only summary embed/view, open to any
    participant (author or admin), not just admins.
    """
    session = _session(kind=TicketKind.ORDER_BOOSTS, game_nick="Scaryyyyy")
    boost_orders = _fake_boost_orders()
    boost_orders.list_lines = AsyncMock(return_value=[MagicMock()])
    cog, *_ = _cog(tickets=_fake_tickets(get_return=session), boost_orders=boost_orders)
    interaction = _interaction(user_id=session.author_id)  # a non-admin author

    await cog._on_order_confirm(interaction)

    interaction.response.send_modal.assert_not_awaited()
    kwargs = interaction.response.edit_message.call_args.kwargs
    assert isinstance(kwargs["view"], OrderSummaryView)
    assert "Заказ бустов" in (kwargs["embed"].title or "")


async def test_order_confirm_rejects_a_non_participant() -> None:
    session = _session(kind=TicketKind.ORDER_BOOSTS, game_nick="Scaryyyyy")
    cog, *_ = _cog(tickets=_fake_tickets(get_return=session))
    interaction = _interaction(user_id=999999)  # neither the author nor an admin

    await cog._on_order_confirm(interaction)

    embed = interaction.response.send_message.call_args.kwargs["embed"]
    assert "Недостаточно прав" in (embed.description or "")


async def test_order_edit_button_opens_the_interactive_editor() -> None:
    """UX #1: "✏️ Редактировать" on the summary switches the message to `OrderEditorView`."""
    session = _session(kind=TicketKind.ORDER_BOOSTS, game_nick="Scaryyyyy")
    cog, *_ = _cog(tickets=_fake_tickets(get_return=session))
    interaction = _interaction(user_id=session.author_id)

    await cog._on_order_edit_button(interaction)

    kwargs = interaction.response.edit_message.call_args.kwargs
    assert isinstance(kwargs["view"], OrderEditorView)
    assert "Редактор заказа" in (kwargs["embed"].title or "")


async def test_order_edit_button_rejects_a_non_participant() -> None:
    session = _session(kind=TicketKind.ORDER_BOOSTS, game_nick="Scaryyyyy")
    cog, *_ = _cog(tickets=_fake_tickets(get_return=session))
    interaction = _interaction(user_id=999999)

    await cog._on_order_edit_button(interaction)

    interaction.response.edit_message.assert_not_awaited()
    embed = interaction.response.send_message.call_args.kwargs["embed"]
    assert "Недостаточно прав" in (embed.description or "")


async def test_order_complete_button_rejects_non_admins() -> None:
    """UX #1: "🏁 Завершить заказ" keeps the old admin-only gate `_on_order_confirm` had."""
    session = _session(kind=TicketKind.ORDER_BOOSTS, game_nick="Scaryyyyy")
    cog, *_ = _cog(tickets=_fake_tickets(get_return=session))
    interaction = _interaction(user_id=session.author_id)  # author, but not an admin

    await cog._on_order_complete_button(interaction)

    interaction.response.send_modal.assert_not_awaited()
    embed = interaction.response.send_message.call_args.kwargs["embed"]
    assert "Недостаточно прав" in (embed.description or "")


async def test_order_complete_button_rejects_an_empty_order() -> None:
    session = _session(kind=TicketKind.ORDER_BOOSTS, game_nick="Scaryyyyy")
    cog, *_ = _cog(tickets=_fake_tickets(get_return=session))
    interaction = _interaction()
    interaction.user.guild_permissions.administrator = True

    await cog._on_order_complete_button(interaction)

    embed = interaction.response.send_message.call_args.kwargs["embed"]
    assert "нет ни одной позиции" in (embed.description or "")


async def test_order_complete_button_opens_the_amount_modal_prefilled_with_the_total() -> None:
    session = _session(kind=TicketKind.ORDER_BOOSTS, game_nick="Scaryyyyy")
    boost_orders = _fake_boost_orders()
    boost_orders.list_lines = AsyncMock(return_value=[MagicMock()])
    boost_orders.compute_order_total = AsyncMock(return_value=Decimal(930000))
    cog, *_ = _cog(tickets=_fake_tickets(get_return=session), boost_orders=boost_orders)
    interaction = _interaction()
    interaction.user.guild_permissions.administrator = True

    await cog._on_order_complete_button(interaction)

    interaction.response.send_modal.assert_awaited_once()
    modal = interaction.response.send_modal.call_args.args[0]
    assert modal.amount.default == "930000"


async def test_amount_submitted_clears_the_draft_for_order_boosts() -> None:
    session = _session(kind=TicketKind.ORDER_BOOSTS, game_nick="Scaryyyyy")
    cog, _tickets, _screenshots, boost_orders, *_ = _cog(tickets=_fake_tickets(get_return=session))
    interaction = _interaction()

    await cog._on_amount_submitted(interaction, "930000")

    boost_orders.clear.assert_awaited_once_with(session.channel_id)


async def test_amount_submitted_does_not_clear_a_draft_for_sell_tickets() -> None:
    session = _session(kind=TicketKind.SELL_ITEMS, game_nick="Scaryyyyy")
    cog, _tickets, _screenshots, boost_orders, *_ = _cog(tickets=_fake_tickets(get_return=session))
    interaction = _interaction()

    await cog._on_amount_submitted(interaction, "100000")

    boost_orders.clear.assert_not_called()


async def test_amount_submitted_notes_the_rank_markup_for_order_boosts() -> None:
    """§9.1, п.2: the confirmed-sum embed shows the applied rank discount/markup."""
    premium = RankLadder().by_key("premium")
    assert premium is not None
    session = _session(kind=TicketKind.ORDER_BOOSTS, game_nick="Scaryyyyy")
    cog, _tickets, _screenshots, boost_orders, *_ = _cog(tickets=_fake_tickets(get_return=session))
    member = MagicMock(spec=discord.Member)
    member.roles = [MagicMock(id=premium.role_id)]
    guild = MagicMock(spec=discord.Guild)
    guild.get_member = MagicMock(return_value=member)
    interaction = _interaction(guild=guild)

    await cog._on_amount_submitted(interaction, "930000")

    embed = interaction.followup.send.call_args.kwargs["embed"]
    description = embed.description or ""
    assert f"<@&{premium.role_id}>" in description
    assert "-0.5%" in description


# -- Module-level helpers -------------------------------------------------


def test_infer_author_id_finds_the_first_non_bot_member_overwrite() -> None:
    member = MagicMock(spec=discord.Member, id=123, bot=False)
    role = MagicMock(spec=discord.Role)
    channel = MagicMock(spec=discord.TextChannel)
    channel.overwrites = {role: MagicMock(), member: MagicMock()}

    assert _infer_author_id(channel) == 123


def test_infer_author_id_returns_zero_when_nobody_found() -> None:
    channel = MagicMock(spec=discord.TextChannel)
    channel.overwrites = {}

    assert _infer_author_id(channel) == 0


def test_resolve_member_by_mention() -> None:
    member = MagicMock(spec=discord.Member, id=456)
    guild = MagicMock(spec=discord.Guild)
    guild.get_member = MagicMock(return_value=member)

    assert _resolve_member(guild, "<@456>") is member


def test_resolve_member_by_display_name() -> None:
    member = MagicMock(spec=discord.Member)
    member.name = "scary"
    member.display_name = "Scaryyyyy"
    guild = MagicMock(spec=discord.Guild)
    guild.members = [member]

    assert _resolve_member(guild, "scaryyyyy") is member


def test_resolve_member_returns_none_without_a_guild() -> None:
    assert _resolve_member(None, "anything") is None
