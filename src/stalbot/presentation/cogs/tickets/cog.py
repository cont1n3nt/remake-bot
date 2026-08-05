"""`TicketsCog` — channel-creation listener and every ticket-flow handler (PLAN.md §11).

Wires the persistent Views/Modals in `views.py`/`order_views.py`/`modals.py`
to `TicketService`/`ScreenshotService`/`BoostOrderService`/
`TransactionService`/`ProgressionService`. No business logic lives here
beyond what genuinely needs a live `discord.Guild`/`discord.Message`
(matching a typed referrer name to a member, waiting for Ticket Tool,
uploading the screenshot) — everything else is a short delegate to a service.
"""

import asyncio
import io
import logging
import re
from collections.abc import Sequence

import discord
from discord.ext import commands

from stalbot.application.dto.boost_order_line import BoostOrderLine
from stalbot.application.dto.ticket_session import TicketSession
from stalbot.application.dto.transaction_request import AddTransactionRequest
from stalbot.application.ports.clock import Clock
from stalbot.application.services.boost_orders import (
    MAX_QUANTITY,
    MIN_QUANTITY,
    BoostOrderService,
)
from stalbot.application.services.progression import ProgressionService
from stalbot.application.services.screenshots import ScreenshotService
from stalbot.application.services.tickets import TicketService
from stalbot.application.services.transactions import TransactionService
from stalbot.config.ids import TICKET_CATEGORIES, TICKET_TOOL_BOT_ID
from stalbot.config.settings import Settings
from stalbot.domain.clock import SystemClock, parse_deadline
from stalbot.domain.entities.item import Item
from stalbot.domain.enums import DealType, DeliveryMethod, TicketKind, TicketStatus
from stalbot.domain.errors import AmountParseError, DeadlineParseError
from stalbot.domain.money import evaluate_amount, format_amount, parse_amount
from stalbot.domain.nick import normalize_nick
from stalbot.presentation.cogs.tickets.card import SCREENSHOT_FILENAME, render_ticket_card
from stalbot.presentation.cogs.tickets.modals import (
    AmountModal,
    OrderBoostsFormModal,
    QuantityModal,
    TicketFormModal,
)
from stalbot.presentation.cogs.tickets.order_card import render_order_editor
from stalbot.presentation.cogs.tickets.order_views import BoostMultiSelectView, OrderEditorView
from stalbot.presentation.cogs.tickets.views import (
    DeliveryMethodView,
    TicketPanelView,
    TicketSummaryView,
)
from stalbot.presentation.embeds.factory import EmbedFactory

logger = logging.getLogger(__name__)

_TOOL_WAIT_TIMEOUT_SECONDS = 30.0
_MAX_SCREENSHOT_BYTES = 8 * 1024 * 1024
_ALLOWED_CONTENT_TYPES = ("image/png", "image/jpeg", "image/webp")
_MENTION_RE = re.compile(r"^<@!?(\d+)>$")
_HANDLED_KINDS = frozenset({TicketKind.SELL_ITEMS, TicketKind.SELL_BOOSTS, TicketKind.ORDER_BOOSTS})

_DEAL_TYPE_OF: dict[TicketKind, DealType] = {
    TicketKind.SELL_ITEMS: DealType.SALE,
    TicketKind.SELL_BOOSTS: DealType.SALE,
    TicketKind.ORDER_BOOSTS: DealType.PURCHASE,
}

_PANEL_DESCRIPTIONS: dict[TicketKind, str] = {
    TicketKind.SELL_ITEMS: "Чтобы оформить сделку, заполните форму по кнопке ниже.",
    TicketKind.SELL_BOOSTS: "Чтобы оформить сделку, заполните форму по кнопке ниже.",
    TicketKind.ORDER_BOOSTS: "Чтобы оформить заказ бустов, заполните форму по кнопке ниже.",
}


class TicketsCog(commands.Cog):
    """Category listener + all handlers for `SELL_ITEMS`/`SELL_BOOSTS`/`ORDER_BOOSTS` tickets."""

    def __init__(
        self,
        tickets: TicketService,
        screenshots: ScreenshotService,
        boost_orders: BoostOrderService,
        transactions: TransactionService,
        progression: ProgressionService,
        embeds: EmbedFactory,
        settings: Settings,
        *,
        clock: Clock | None = None,
        tool_wait_timeout_seconds: float = _TOOL_WAIT_TIMEOUT_SECONDS,
    ) -> None:
        """Wire the cog to the services it delegates to.

        Args:
            tickets: Persists `ticket_sessions` state through the flow.
            screenshots: Hashes/archives/OCRs a screenshot once uploaded.
            boost_orders: Draft-line CRUD and live pricing for `ORDER_BOOSTS`.
            transactions: Shared with `/add` — records the confirmed deal.
            progression: Reconciles roles and announces promotions afterwards.
            embeds: Builds every embed this cog sends.
            settings: For `log_channel_id`.
            clock: Time source for `parse_deadline`. Defaults to `SystemClock()`.
            tool_wait_timeout_seconds: How long to wait for Ticket Tool's
                first message before posting the panel anyway (PLAN.md
                §11.2). Overridable so tests don't block for 30 real seconds.
        """
        self._tickets = tickets
        self._screenshots = screenshots
        self._boost_orders = boost_orders
        self._transactions = transactions
        self._progression = progression
        self._embeds = embeds
        self._settings = settings
        self._clock = clock or SystemClock()
        self._tool_wait_timeout = tool_wait_timeout_seconds
        self._tool_wait: dict[int, asyncio.Event] = {}
        # UX #15: only images sent after the "📸 Прикрепить скриншот" button was
        # pressed should be recorded — an image posted into the ticket channel
        # unprompted (chit-chat, an unrelated screenshot) must not silently
        # become part of the dataset/log. Populated in `_on_screenshot_button`,
        # cleared once a screenshot is actually recorded for that channel.
        self._awaiting_screenshot: set[int] = set()

    def persistent_views(self) -> tuple[discord.ui.View, ...]:
        """Every persistent View this cog owns, for `bot.add_view()` at startup."""
        return (
            TicketPanelView(TicketKind.SELL_ITEMS, self._on_start),
            TicketPanelView(TicketKind.SELL_BOOSTS, self._on_start),
            TicketPanelView(TicketKind.ORDER_BOOSTS, self._on_start),
            TicketSummaryView(self._on_screenshot_button, self._on_confirm_button),
            self._build_order_editor_view(None, ()),
        )

    # -- Channel lifecycle (PLAN.md §11.2) -----------------------------------

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        """Track a new ticket channel and post its panel once Ticket Tool has spoken."""
        if not isinstance(channel, discord.TextChannel):
            return
        kind = TICKET_CATEGORIES.get(channel.category_id or 0)
        if kind not in _HANDLED_KINDS:
            return

        # TICK-7: registered before the `await` below (not after), narrowing
        # the window where Ticket Tool's first message could be dispatched
        # and processed by `on_message` before this channel is tracked here
        # — a miss just means a needless wait for the full timeout, not a
        # crash, but there is no reason to leave any of the gap open.
        event = asyncio.Event()
        self._tool_wait[channel.id] = event
        try:
            await self._tickets.open_ticket(channel.id, kind, _infer_author_id(channel))
            try:
                await asyncio.wait_for(event.wait(), timeout=self._tool_wait_timeout)
            except TimeoutError:
                pass
        finally:
            self._tool_wait.pop(channel.id, None)

        try:
            await self._post_panel(channel, kind)
        except discord.HTTPException:
            # TICK-6: the channel can be gone (deleted by Ticket Tool
            # automation, or an admin) by the time the wait above finishes —
            # nothing left to post to, and nothing more this listener can do.
            logger.warning(
                "could not post the ticket panel: channel %s unavailable", channel.id, exc_info=True
            )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Detect Ticket Tool's first message, and any screenshot sent afterwards."""
        if message.author.id == TICKET_TOOL_BOT_ID:
            event = self._tool_wait.get(message.channel.id)
            if event is not None:
                event.set()
            return

        if message.author.bot or not message.attachments:
            return
        if message.channel.id not in self._awaiting_screenshot:
            # UX #15: no "📸 Прикрепить скриншот" click is on file for this
            # channel — this image was not requested, ignore it entirely.
            return
        session = await self._tickets.get(message.channel.id)
        if session is None:
            return
        attachments = _image_attachments(message.attachments)
        if not attachments:
            return
        await self._handle_screenshots(message, session, attachments)

    async def _post_panel(self, channel: discord.TextChannel, kind: TicketKind) -> None:
        embed = self._embeds.ticket(kind, _PANEL_DESCRIPTIONS[kind])
        view = TicketPanelView(kind, self._on_start)
        message = await channel.send(embed=embed, view=view)
        await self._tickets.record_panel(channel.id, message.id)

    # -- Form flow (PLAN.md §11.3, §11.4) ------------------------------------

    async def _on_start(self, interaction: discord.Interaction, kind: TicketKind) -> None:
        if kind is TicketKind.ORDER_BOOSTS:
            # No delivery method for a boost order — straight to the form
            # modal, same as any other button-triggered modal (PLAN.md §11.4).
            await interaction.response.send_modal(
                OrderBoostsFormModal(self._on_order_form_submitted, embeds=self._embeds)
            )
            return

        embed = self._embeds.info(
            "📮 Выберите способ передачи",
            "Ник: Scaryyyyy\n"
            "Отправлять предметы / деньги на этот ник при выборе «Почта».",
        )
        view = DeliveryMethodView(self._on_delivery_selected)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def _on_delivery_selected(
        self, interaction: discord.Interaction, method: DeliveryMethod
    ) -> None:
        await self._tickets.record_delivery_method(interaction.channel_id or 0, method)
        await interaction.response.send_modal(
            TicketFormModal(self._on_form_submitted, embeds=self._embeds)
        )

    async def _on_form_submitted(
        self,
        interaction: discord.Interaction,
        nick: str,
        referrer_nick: str | None,
        referrer_discord_text: str | None,
    ) -> None:
        referrer_nick, referrer_discord_text = _drop_self_referral(
            nick, referrer_nick, referrer_discord_text
        )
        referrer_member = (
            _resolve_member(interaction.guild, referrer_discord_text)
            if referrer_discord_text
            else None
        )
        channel_id = interaction.channel_id or 0
        # TICK-2: whoever actually fills in and submits the form is the
        # ticket's author — a far stronger signal than whoever first clicked
        # the shared persistent "Заполнить заявку" button (which could be
        # staff with category-role access, not the real opener), and unlike
        # that first click, this can't lock in a wrong guess: the form can
        # only be submitted once per session before status leaves `FILLED`.
        await self._tickets.set_author(channel_id, interaction.user.id)
        session = await self._tickets.record_form(
            channel_id,
            game_nick=nick,
            referrer_nick=referrer_nick,
            referrer_discord_id=referrer_member.id if referrer_member else None,
        )

        await interaction.response.defer(ephemeral=True)
        channel = interaction.channel
        if isinstance(channel, discord.TextChannel):
            await self._post_or_update_summary(channel, session)

        embed = self._embeds.success(
            "✅ Заявка заполнена", "Спасибо! Ваша заявка передана администрации."
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def _on_order_form_submitted(
        self,
        interaction: discord.Interaction,
        nick: str,
        deadline_text: str,
        referrer_nick: str | None,
        referrer_discord_text: str | None,
    ) -> None:
        try:
            deadline = parse_deadline(deadline_text, now=self._clock.now())
        except DeadlineParseError as exc:
            # Reopen the modal with the error as the deadline field's
            # placeholder and every other field preserved (PLAN.md §11.4).
            await interaction.response.send_modal(
                OrderBoostsFormModal(
                    self._on_order_form_submitted,
                    embeds=self._embeds,
                    nick=nick,
                    deadline_text=deadline_text,
                    referrer_nick=referrer_nick or "",
                    referrer_discord_text=referrer_discord_text or "",
                    error_hint=str(exc),
                )
            )
            return

        referrer_nick, referrer_discord_text = _drop_self_referral(
            nick, referrer_nick, referrer_discord_text
        )
        referrer_member = (
            _resolve_member(interaction.guild, referrer_discord_text)
            if referrer_discord_text
            else None
        )
        channel_id = interaction.channel_id or 0
        await self._tickets.set_author(channel_id, interaction.user.id)  # TICK-2, see above
        session = await self._tickets.record_form(
            channel_id,
            game_nick=nick,
            referrer_nick=referrer_nick,
            referrer_discord_id=referrer_member.id if referrer_member else None,
            deadline=deadline,
        )

        await interaction.response.defer(ephemeral=True)
        channel = interaction.channel
        if isinstance(channel, discord.TextChannel):
            await self._post_or_update_order_editor(channel, session)

        embed = self._embeds.success(
            "✅ Заявка заполнена", "Спасибо! Ваша заявка передана администрации."
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def _post_or_update_summary(
        self, channel: discord.TextChannel, session: TicketSession
    ) -> None:
        embed = render_ticket_card(session, self._embeds)
        view = TicketSummaryView(self._on_screenshot_button, self._on_confirm_button)
        if session.summary_message_id is not None:
            message = await _try_fetch(channel, session.summary_message_id)
            if message is not None:
                await message.edit(embed=embed, view=view)
                return
        message = await channel.send(embed=embed, view=view)
        await self._tickets.record_summary_message(channel.id, message.id)

    # -- Boost-order editor (PLAN.md §11.6) ----------------------------------

    async def _on_order_line_selected(self, interaction: discord.Interaction, item_id: int) -> None:
        session = await self._require_order_participant(interaction)
        if session is None:
            return
        await self._tickets.set_active_order_item(session.channel_id, item_id)
        await self._refresh_order_editor_inline(interaction)

    async def _on_order_qty_plus(self, interaction: discord.Interaction) -> None:
        await self._adjust_order_quantity(interaction, 1)

    async def _on_order_qty_minus(self, interaction: discord.Interaction) -> None:
        await self._adjust_order_quantity(interaction, -1)

    async def _adjust_order_quantity(self, interaction: discord.Interaction, delta: int) -> None:
        session = await self._active_order_session(interaction)
        if session is None or session.active_order_item_id is None:
            return
        await self._boost_orders.adjust_quantity(
            session.channel_id, session.active_order_item_id, delta
        )
        await self._refresh_order_editor_inline(interaction)

    async def _on_order_qty_input(self, interaction: discord.Interaction) -> None:
        session = await self._active_order_session(interaction)
        if session is None or session.active_order_item_id is None:
            return
        await interaction.response.send_modal(
            QuantityModal(self._on_order_qty_submitted, embeds=self._embeds)
        )

    async def _on_order_qty_submitted(
        self, interaction: discord.Interaction, qty_text: str
    ) -> None:
        channel_id = interaction.channel_id or 0
        session = await self._tickets.get(channel_id)
        if session is None:
            await interaction.response.defer(ephemeral=True)
            return
        if not (interaction.user.id == session.author_id or _is_admin(interaction.user)):
            embed = self._embeds.error("Ошибка", "Недостаточно прав для этого действия.")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        if session.status is TicketStatus.CONFIRMED:  # TICK-3, see `_require_order_participant`
            embed = self._embeds.warning("⚠️ Уже подтверждено", "Эта заявка уже была подтверждена.")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        if session.active_order_item_id is None:
            await interaction.response.defer(ephemeral=True)
            return

        try:
            parsed_quantity = parse_amount(qty_text)
        except AmountParseError:
            embed = self._embeds.error("Ошибка", "Не удалось распознать количество.")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        quantity = int(parsed_quantity)
        if quantity != parsed_quantity:
            # TICK-10: reject a fractional quantity instead of silently
            # truncating it — "9999.9" becoming "9999" with no feedback
            # would look like the modal just dropped a digit.
            embed = self._embeds.error("Ошибка", "Количество должно быть целым числом.")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        if not (MIN_QUANTITY <= quantity <= MAX_QUANTITY):
            embed = self._embeds.error(
                "Ошибка", f"Количество должно быть от {MIN_QUANTITY} до {MAX_QUANTITY}."
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await self._boost_orders.set_quantity(
            session.channel_id, session.active_order_item_id, quantity
        )
        await interaction.response.defer(ephemeral=True)
        channel = interaction.channel
        if isinstance(channel, discord.TextChannel):
            await self._post_or_update_order_editor(channel, session)

    async def _on_order_delete_line(self, interaction: discord.Interaction) -> None:
        session = await self._active_order_session(interaction)
        if session is None or session.active_order_item_id is None:
            return
        await self._boost_orders.remove_line(session.channel_id, session.active_order_item_id)
        await self._tickets.set_active_order_item(session.channel_id, None)
        await self._refresh_order_editor_inline(interaction)

    async def _require_order_participant(
        self, interaction: discord.Interaction
    ) -> TicketSession | None:
        """Fetch the session, rejecting anyone but its author or an admin (PLAN.md §11.6).

        Also rejects once the ticket is `CONFIRMED` (TICK-3): every other
        editor handler routes through this (or `_active_order_session`,
        which wraps it) except the confirm button itself, which already had
        this check (`_confirm_precheck`) — without it here too, the draft
        stayed mutable forever after confirmation, resurrecting lines
        `TransactionService`'s post-confirm side effects already cleared.
        """
        session = await self._tickets.get(interaction.channel_id or 0)
        if session is None:
            return None
        if not (interaction.user.id == session.author_id or _is_admin(interaction.user)):
            embed = self._embeds.error("Ошибка", "Недостаточно прав для этого действия.")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return None
        if session.status is TicketStatus.CONFIRMED:
            embed = self._embeds.warning("⚠️ Уже подтверждено", "Эта заявка уже была подтверждена.")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return None
        return session

    async def _active_order_session(self, interaction: discord.Interaction) -> TicketSession | None:
        """`_require_order_participant`, plus a warning if no line is currently selected."""
        session = await self._require_order_participant(interaction)
        if session is None:
            return None
        if session.active_order_item_id is None:
            embed = self._embeds.warning(
                "⚠️ Ничего не выбрано", "Сначала выберите буст в списке выше."
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return None
        return session

    async def _on_order_add_boosts(self, interaction: discord.Interaction) -> None:
        session = await self._require_order_participant(interaction)
        if session is None:
            return
        catalog = await self._boost_orders.list_available_boosts()
        lines = await self._boost_orders.list_lines(session.channel_id)
        selected_ids = frozenset(line.item_id for line in lines)
        quantities = {line.item_id: line.quantity for line in lines}
        view = BoostMultiSelectView(
            catalog,
            selected_ids,
            author_id=interaction.user.id,
            embeds=self._embeds,
            on_change=self._on_order_boosts_changed,
            quantities=quantities,
        )
        await interaction.response.send_message(
            embed=view.status_embed(), view=view, ephemeral=True
        )
        view.message = await interaction.original_response()

    async def _on_order_boosts_changed(
        self,
        interaction: discord.Interaction,
        page_items: Sequence[Item],
        chosen_ids: frozenset[int],
    ) -> frozenset[int]:
        channel_id = interaction.channel_id or 0
        rejected = await self._boost_orders.apply_page_selection(channel_id, page_items, chosen_ids)
        channel = interaction.channel
        if isinstance(channel, discord.TextChannel):
            session = await self._tickets.get(channel_id)
            if session is not None:
                await self._post_or_update_order_editor(channel, session)
        return rejected

    async def _on_order_confirm(self, interaction: discord.Interaction) -> None:
        session = await self._confirm_precheck(interaction)
        if session is None:
            return
        lines = await self._boost_orders.list_lines(session.channel_id)
        if not lines:
            embed = self._embeds.error("Ошибка", "В заказе нет ни одной позиции.")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        total = await self._boost_orders.compute_total(session.channel_id)
        await interaction.response.send_modal(
            AmountModal(self._on_amount_submitted, embeds=self._embeds, default=str(int(total)))
        )

    async def _refresh_order_editor_inline(self, interaction: discord.Interaction) -> None:
        """Re-render in response to a component click on the editor message itself."""
        rendered = await self._render_order(interaction.channel_id or 0)
        if rendered is None:
            return
        _session, embed, view = rendered
        await interaction.response.edit_message(embed=embed, view=view)

    async def _post_or_update_order_editor(
        self, channel: discord.TextChannel, session: TicketSession
    ) -> None:
        """Post the editor for the first time, or edit it after a modal/ephemeral-select change."""
        rendered = await self._render_order(channel.id)
        if rendered is None:
            return
        _session, embed, view = rendered
        if session.summary_message_id is not None:
            message = await _try_fetch(channel, session.summary_message_id)
            if message is not None:
                await message.edit(embed=embed, view=view)
                return
        message = await channel.send(embed=embed, view=view)
        await self._tickets.record_summary_message(channel.id, message.id)

    async def _render_order(
        self, channel_id: int
    ) -> tuple[TicketSession, discord.Embed, OrderEditorView] | None:
        session = await self._tickets.get(channel_id)
        if session is None:
            return None
        lines_with_items = await self._boost_orders.list_lines_with_items(channel_id)
        embed = render_order_editor(session, lines_with_items, self._embeds)
        view = self._build_order_editor_view(session.active_order_item_id, lines_with_items)
        return session, embed, view

    def _build_order_editor_view(
        self,
        active_item_id: int | None,
        lines_with_items: Sequence[tuple[BoostOrderLine, Item | None]],
    ) -> OrderEditorView:
        options = [
            discord.SelectOption(
                label=f"{item.name} × {line.quantity}"[:100],
                value=str(line.item_id),
                default=active_item_id == line.item_id,
            )
            for line, item in lines_with_items
            if item is not None
        ]
        return OrderEditorView(
            options,
            on_select=self._on_order_line_selected,
            on_plus=self._on_order_qty_plus,
            on_minus=self._on_order_qty_minus,
            on_input_qty=self._on_order_qty_input,
            on_delete=self._on_order_delete_line,
            on_add=self._on_order_add_boosts,
            on_confirm=self._on_order_confirm,
        )

    # -- Screenshot (PLAN.md §11.5) -------------------------------------------

    async def _on_screenshot_button(self, interaction: discord.Interaction) -> None:
        embed = self._embeds.info(
            "📸 Прикрепите скриншот",
            "Отправьте скриншот следующим сообщением в этом канале.\n"
            "Требования: полный экран, без обрезки интерфейса, читаемые цифры, "
            "PNG / JPG / WEBP, не более 8 МБ.",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        self._awaiting_screenshot.add(interaction.channel_id or 0)

    async def _handle_screenshots(
        self,
        message: discord.Message,
        session: TicketSession,
        attachments: Sequence[discord.Attachment],
    ) -> None:
        oversized = [a for a in attachments if a.size > _MAX_SCREENSHOT_BYTES]
        attachments = [a for a in attachments if a.size <= _MAX_SCREENSHOT_BYTES]
        if oversized:
            embed = self._embeds.warning(
                "⚠️ Скриншот слишком большой",
                f"Максимум {_MAX_SCREENSHOT_BYTES // (1024 * 1024)} МБ — сожмите изображение "
                "и отправьте снова.",
            )
            await message.channel.send(embed=embed)
        if not attachments:
            return

        # UX #13: every image attached to the message is archived/analyzed —
        # not just the first one. The card and `TicketSession.screenshot_url`
        # can only ever point at one cover image (an embed has a single
        # `image`), so the first attachment is what gets recorded there.
        # Archiving/recording happens before the `on_attached` (OCR/dataset)
        # pass below, same order the single-attachment path always used —
        # a caller-side bug leaking out of `on_attached` must not prevent
        # the card/log-channel archive from having already landed.
        archived: list[tuple[bytes, str, str | None]] = []
        for attachment in attachments:
            data = await attachment.read()
            mime = attachment.content_type or "image/png"
            image_url = await self._archive_to_log_channel(message, session, data)
            archived.append((data, mime, image_url))

        cover_data, _cover_mime, cover_url = archived[0]
        updated = await self._tickets.record_screenshot(session.channel_id, cover_url, message.id)

        if updated.summary_message_id is not None and isinstance(
            message.channel, discord.TextChannel
        ):
            summary_message = await _try_fetch(message.channel, updated.summary_message_id)
            if summary_message is not None:
                card_file = discord.File(io.BytesIO(cover_data), filename=SCREENSHOT_FILENAME)
                embed = render_ticket_card(updated, self._embeds)
                view = TicketSummaryView(self._on_screenshot_button, self._on_confirm_button)
                await summary_message.edit(embed=embed, view=view, attachments=[card_file])

        self._awaiting_screenshot.discard(session.channel_id)

        # UX #2: the raw upload is now preserved in the ticket card's embed
        # (and archived in the log channel) — no reason to leave the
        # original message cluttering the channel too.
        try:
            await message.delete()
        except discord.HTTPException:
            logger.warning(
                "could not delete the screenshot message %s in channel %s",
                message.id,
                session.channel_id,
                exc_info=True,
            )

        # UX #12: a plain channel message has no `ephemeral` concept (that
        # only exists for interaction responses) — `delete_after` is the
        # closest equivalent, a transient confirmation instead of one that
        # lingers in the channel forever.
        count_note = f" ({len(attachments)} шт.)" if len(attachments) > 1 else ""
        confirmation = self._embeds.success(
            "✅ Скриншот закреплён", f"Скриншот закреплён в заявку{count_note}."
        )
        await message.channel.send(
            content=f"<@{message.author.id}>", embed=confirmation, delete_after=8.0
        )

        # OCR/dataset bookkeeping runs last and is fully isolated from
        # everything above: decision A7 requires OCR to never affect the
        # ticket flow, and `on_attached` already swallows its own OCR
        # errors (APP-8) — this `except Exception` is a second line of
        # defense against a *caller*-side bug in that layer (e.g. the
        # dataset/analysis bookkeeping around the OCR call) reaching here
        # and undoing the gate-close/delete/confirmation that already
        # completed above.
        for data, mime, image_url in archived:
            try:
                await self._screenshots.on_attached(
                    session.channel_id,
                    data,
                    filename=SCREENSHOT_FILENAME,
                    mime=mime,
                    image_url=image_url,
                )
            except Exception:
                logger.exception(
                    "screenshot analysis failed for channel %s — ticket flow unaffected",
                    session.channel_id,
                )

    async def _archive_to_log_channel(
        self, message: discord.Message, session: TicketSession, data: bytes
    ) -> str | None:
        """Re-upload the screenshot to the log channel — a permanent CDN link (PLAN.md §11.5)."""
        log_channel = (
            message.guild.get_channel(self._settings.log_channel_id) if message.guild else None
        )
        if not isinstance(log_channel, discord.TextChannel):
            logger.warning("screenshot archive skipped: log channel not visible to the bot")
            return None

        embed = render_ticket_card(session, self._embeds)
        embed.set_image(url=f"attachment://{SCREENSHOT_FILENAME}")
        file = discord.File(io.BytesIO(data), filename=SCREENSHOT_FILENAME)
        log_message = await log_channel.send(embed=embed, file=file)
        return log_message.embeds[0].image.url if log_message.embeds else None

    # -- Confirmation (PLAN.md §11.5, shared with `/add`) --------------------

    async def _confirm_precheck(self, interaction: discord.Interaction) -> TicketSession | None:
        """Shared admin/filled/not-already-confirmed checks for both confirm buttons.

        Responds with the appropriate error/warning embed and returns
        `None` if any check fails; returns the session otherwise.
        """
        if not _is_admin(interaction.user):
            embed = self._embeds.error("Ошибка", "Недостаточно прав для этого действия.")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return None

        session = await self._tickets.get(interaction.channel_id or 0)
        if session is None or session.game_nick is None:
            embed = self._embeds.error("Ошибка", "Заявка ещё не заполнена.")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return None
        if session.status is TicketStatus.CONFIRMED:
            embed = self._embeds.warning("⚠️ Уже подтверждено", "Эта заявка уже была подтверждена.")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return None
        return session

    async def _on_confirm_button(self, interaction: discord.Interaction) -> None:
        session = await self._confirm_precheck(interaction)
        if session is None:
            return
        await interaction.response.send_modal(
            AmountModal(self._on_amount_submitted, embeds=self._embeds)
        )

    async def _on_amount_submitted(
        self, interaction: discord.Interaction, amount_text: str
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        session = await self._tickets.get(interaction.channel_id or 0)
        assert session is not None and session.game_nick is not None  # noqa: S101 - checked in _on_confirm_button
        if session.status is TicketStatus.CONFIRMED:
            # Re-checked here, not just in `_confirm_precheck` before the modal was
            # shown: two admins (or one double-clicking) can both pass that earlier
            # check before either finishes filling in the modal. This catches the
            # *staggered* case, where one submission's `record_confirmed()` below
            # has already landed by the time this one reads the session. A truly
            # simultaneous double-submit (both read the session before either has
            # confirmed) slips past this check too — that's caught below instead,
            # via `result.replayed`.
            embed = self._embeds.warning("⚠️ Уже подтверждено", "Эта заявка уже была подтверждена.")
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        amount = evaluate_amount(amount_text)
        request = AddTransactionRequest(
            nick=session.game_nick,
            deal_type=_DEAL_TYPE_OF[session.kind],
            amount=amount,
            discord_id=session.author_id,
            idempotency_key=f"ticket:{session.channel_id}",
            referrer_nick=session.referrer_nick,
            force_rebind=False,
        )
        result = await self._transactions.register(request)
        if result.replayed:
            # `TransactionService`'s lock (CLUSTER-1) guarantees only one concurrent
            # submission actually wrote — this one lost the race. The winner already
            # ran every post-confirm side effect below (screenshots, progression
            # sync, the public announcement); running them again here would just
            # duplicate them for the same deal. Still mark the session confirmed
            # (idempotent) so it doesn't get stuck if this happened to be the call
            # that reached here first.
            await self._tickets.record_confirmed(session.channel_id)
            embed = self._embeds.success(
                "✅ Сделка уже зафиксирована",
                f"Сумма: {format_amount(result.record.amount)} — строка {result.record.row}.",
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        await self._tickets.record_confirmed(session.channel_id)
        await self._screenshots.record_confirmed_amount(session.channel_id, amount)
        if session.kind is TicketKind.ORDER_BOOSTS:
            await self._boost_orders.clear(session.channel_id)

        sync_nicks = [normalize_nick(session.game_nick)]
        if session.referrer_nick:
            sync_nicks.append(normalize_nick(session.referrer_nick))
        channel = interaction.channel
        if isinstance(channel, discord.abc.Messageable):
            await self._progression.sync(sync_nicks, announce_to=channel)

        embed = self._embeds.success(
            "✅ Сделка зафиксирована",
            f"Сумма: {format_amount(result.record.amount)} — строка {result.record.row}.",
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        if isinstance(channel, discord.abc.Messageable):
            await channel.send(
                embed=self._embeds.success(
                    "🧾 Заявка подтверждена",
                    f"Сделка зафиксирована администратором {interaction.user.mention}.",
                )
            )


def _is_admin(user: discord.User | discord.Member) -> bool:
    return isinstance(user, discord.Member) and user.guild_permissions.administrator


def _infer_author_id(channel: discord.TextChannel) -> int:
    """Best-effort guess at the ticket's opener from the channel's permission overwrites.

    `on_guild_channel_create` carries no explicit "who opened this" field;
    Ticket Tool grants the opener an explicit member-level overwrite, which
    this picks out. `0` (never a valid Discord id) means "unknown yet" —
    this is only ever a placeholder until the form is actually submitted
    (TICK-2), never trusted for access control before then.
    """
    for target in channel.overwrites:
        if isinstance(target, discord.Member) and not target.bot:
            return target.id
    return 0


def _resolve_member(guild: discord.Guild | None, text: str) -> discord.Member | None:
    """Match a typed referrer name (mention, username, or display name) to a guild member."""
    if guild is None:
        return None
    stripped = text.strip()
    mention_match = _MENTION_RE.match(stripped)
    if mention_match:
        return guild.get_member(int(mention_match.group(1)))
    lowered = stripped.lower().lstrip("@")
    for member in guild.members:
        if member.name.lower() == lowered or member.display_name.lower() == lowered:
            return member
    return None


def _drop_self_referral(
    nick: str, referrer_nick: str | None, referrer_discord_text: str | None
) -> tuple[str | None, str | None]:
    """Discard a referral where the typed referrer nick is the submitter's own (TICK-8).

    Same rule `/set_referral` already enforces (`presentation/cogs/manual.py`)
    for admin-entered referrals — applied here too so a player can't credit
    themselves through the ticket form instead. Silently dropped (treated as
    if the field was left blank) rather than rejecting the whole submission,
    matching how an unresolved `referrer_discord_text` is already handled.

    Only compares nicks: a blank `referrer_nick` with a self-mentioning
    `referrer_discord_text` slips through untouched. Inert today —
    `TransactionService` only credits off `referrer_nick`, and the card only
    shows `referrer_discord_id` alongside a truthy `referrer_nick` — but
    revisit this if `referrer_discord_id` ever starts being trusted on its
    own.
    """
    if referrer_nick is not None and normalize_nick(referrer_nick) == normalize_nick(nick):
        return None, None
    return referrer_nick, referrer_discord_text


def _image_attachments(
    attachments: Sequence[discord.Attachment],
) -> list[discord.Attachment]:
    return [
        attachment
        for attachment in attachments
        if (attachment.content_type or "").split(";")[0].strip().lower()
        in _ALLOWED_CONTENT_TYPES
    ]


async def _try_fetch(channel: discord.TextChannel, message_id: int) -> discord.Message | None:
    try:
        return await channel.fetch_message(message_id)
    except discord.NotFound:
        return None
