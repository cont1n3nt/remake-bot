"""Modals for the ticket flow (PLAN.md §11.4, §11.5).

Fields are added in `__init__` rather than as class attributes so each
modal can carry the callback (and, for `AmountModal`, the pre-filled
`default`) it was constructed with — plain functions, kept out of the
application layer the same way `views.py`'s Views are.
"""

from collections.abc import Awaitable, Callable

import discord

from stalbot.presentation.embeds.factory import EmbedFactory
from stalbot.presentation.views.error_modal import ErrorReportingModal

_FormSubmitHandler = Callable[[discord.Interaction, str, str | None, str | None], Awaitable[None]]
_AmountSubmitHandler = Callable[[discord.Interaction, str], Awaitable[None]]
_OrderFormSubmitHandler = Callable[
    [discord.Interaction, str, str, str | None, str | None], Awaitable[None]
]

_NICK_MAX_LENGTH = 100
_AMOUNT_MAX_LENGTH = 64
_DEADLINE_MAX_LENGTH = 50
_DEADLINE_LABEL = "До какой даты и времени нужно сделать"
_DEADLINE_PLACEHOLDER = "31.07.2026 21:00 (по МСК)"


class TicketFormModal(ErrorReportingModal):
    """Ник + optional referrer fields, shared by `SELL_ITEMS` and `SELL_BOOSTS` (PLAN.md §11.4)."""

    def __init__(self, on_submit: _FormSubmitHandler, *, embeds: EmbedFactory) -> None:
        """Build the modal.

        Args:
            on_submit: Called with the interaction, the typed nick, and the
                two optional referrer fields (each `None` if left blank).
            embeds: Factory used to build the error embed on an `on_submit` failure.
        """
        super().__init__(title="📝 Заявка", embeds=embeds)
        self._on_submit_cb = on_submit
        self.nick: discord.ui.TextInput[TicketFormModal] = discord.ui.TextInput(
            label="Ваш игровой ник", max_length=_NICK_MAX_LENGTH
        )
        self.referrer_nick: discord.ui.TextInput[TicketFormModal] = discord.ui.TextInput(
            label="Кто пригласил (в игре)", required=False, max_length=_NICK_MAX_LENGTH
        )
        self.referrer_discord: discord.ui.TextInput[TicketFormModal] = discord.ui.TextInput(
            label="Кто пригласил (Discord)", required=False, max_length=_NICK_MAX_LENGTH
        )
        self.add_item(self.nick)
        self.add_item(self.referrer_nick)
        self.add_item(self.referrer_discord)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Forward the filled-in fields to the injected handler."""
        await self._on_submit_cb(
            interaction,
            str(self.nick.value),
            str(self.referrer_nick.value) or None,
            str(self.referrer_discord.value) or None,
        )


class AmountModal(ErrorReportingModal):
    """The one `💰 Сумма сделки` field, shared by `/add`-equivalent ticket confirmation."""

    def __init__(
        self,
        on_submit: _AmountSubmitHandler,
        *,
        embeds: EmbedFactory,
        default: str | None = None,
    ) -> None:
        """Build the modal.

        Args:
            on_submit: Called with the interaction and the raw typed amount
                (parsed by the caller via `evaluate_amount`).
            embeds: Factory used to build the error embed on an `on_submit` failure.
            default: Pre-filled value. Always `None` in v1.0 — the
                parameter exists so M13's OCR estimate can prefill it
                without touching this modal (PLAN.md §11.8).
        """
        super().__init__(title="💰 Сумма сделки", embeds=embeds)
        self._on_submit_cb = on_submit
        self.amount: discord.ui.TextInput[AmountModal] = discord.ui.TextInput(
            label="Сумма сделки",
            placeholder="299 900 или 250к",
            default=default,
            max_length=_AMOUNT_MAX_LENGTH,
        )
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Forward the typed amount to the injected handler."""
        await self._on_submit_cb(interaction, str(self.amount.value))


class OrderBoostsFormModal(ErrorReportingModal):
    """Ник + срок + optional referrer fields, for `ORDER_BOOSTS` (PLAN.md §11.4).

    Discord has no date-picker in modals, so the deadline is free text
    parsed by `domain.clock.parse_deadline`. On a parse error the caller
    reopens this same modal (a fresh instance, `nick`/`referrer_*`
    pre-filled from what was just typed, `error_hint` shown in the
    deadline field's placeholder) rather than losing the other fields.
    """

    def __init__(
        self,
        on_submit: _OrderFormSubmitHandler,
        *,
        embeds: EmbedFactory,
        nick: str = "",
        deadline_text: str = "",
        referrer_nick: str = "",
        referrer_discord_text: str = "",
        error_hint: str | None = None,
    ) -> None:
        """Build the modal.

        Args:
            on_submit: Called with the interaction, the typed nick, the
                typed deadline text (unparsed — the caller parses it), and
                the two optional referrer fields (each `None` if blank).
            embeds: Factory used to build the error embed on an `on_submit` failure.
            nick: Pre-filled nick, when reopening after a validation error.
            deadline_text: Pre-filled deadline text, same case.
            referrer_nick: Pre-filled referrer nick, same case.
            referrer_discord_text: Pre-filled referrer Discord text, same case.
            error_hint: If set, replaces the deadline field's placeholder
                with this message instead of the usual format example.
        """
        super().__init__(title="📝 Заявка на заказ бустов", embeds=embeds)
        self._on_submit_cb = on_submit
        self.nick: discord.ui.TextInput[OrderBoostsFormModal] = discord.ui.TextInput(
            label="Ваш игровой ник", default=nick or None, max_length=_NICK_MAX_LENGTH
        )
        self.deadline: discord.ui.TextInput[OrderBoostsFormModal] = discord.ui.TextInput(
            label=_DEADLINE_LABEL,
            placeholder=(error_hint or _DEADLINE_PLACEHOLDER)[:_DEADLINE_MAX_LENGTH],
            # TICK-4: Discord only shows `placeholder` on an empty field —
            # `default` would win and hide the error hint, so the rejected
            # text is deliberately NOT carried over into `default` here (every
            # other field still is). The player retypes the one field that
            # actually needs fixing instead of seeing no explanation at all.
            default=None if error_hint else (deadline_text or None),
            max_length=_DEADLINE_MAX_LENGTH,
        )
        self.referrer_nick: discord.ui.TextInput[OrderBoostsFormModal] = discord.ui.TextInput(
            label="Кто пригласил (в игре)",
            required=False,
            default=referrer_nick or None,
            max_length=_NICK_MAX_LENGTH,
        )
        self.referrer_discord: discord.ui.TextInput[OrderBoostsFormModal] = discord.ui.TextInput(
            label="Кто пригласил (Discord)",
            required=False,
            default=referrer_discord_text or None,
            max_length=_NICK_MAX_LENGTH,
        )
        self.add_item(self.nick)
        self.add_item(self.deadline)
        self.add_item(self.referrer_nick)
        self.add_item(self.referrer_discord)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Forward the filled-in fields to the injected handler."""
        await self._on_submit_cb(
            interaction,
            str(self.nick.value),
            str(self.deadline.value),
            str(self.referrer_nick.value) or None,
            str(self.referrer_discord.value) or None,
        )


class QuantityModal(ErrorReportingModal):
    """The one `🔢 Ввести количество` field for the active boost-order line (PLAN.md §11.6)."""

    def __init__(self, on_submit: _AmountSubmitHandler, *, embeds: EmbedFactory) -> None:
        """Build the modal.

        Args:
            on_submit: Called with the interaction and the raw typed
                quantity (parsed by the caller via `domain.money.parse_amount`).
            embeds: Factory used to build the error embed on an `on_submit` failure.
        """
        super().__init__(title="🔢 Количество", embeds=embeds)
        self._on_submit_cb = on_submit
        self.quantity: discord.ui.TextInput[QuantityModal] = discord.ui.TextInput(
            label="Количество", placeholder="1", max_length=8
        )
        self.add_item(self.quantity)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Forward the typed quantity to the injected handler."""
        await self._on_submit_cb(interaction, str(self.quantity.value))
