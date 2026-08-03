"""Persistent and short-lived Views for the ticket flow (PLAN.md §11.2–§11.5, §11.7).

`TicketPanelView` and `TicketSummaryView` are persistent: their `custom_id`s
are deterministic strings (`ticket:start:{kind}`, `ticket:screenshot`,
`ticket:confirm`), so `bot.add_view()` at startup makes every button on
every old message — across a restart — resolve back to the same handler,
regardless of which specific message or channel it lives on. Handlers are
injected as plain callables rather than the view reaching into a service
itself, keeping this module free of any application-layer dependency.
"""

from collections.abc import Awaitable, Callable

import discord

from stalbot.domain.enums import DeliveryMethod, TicketKind

_StartHandler = Callable[[discord.Interaction, TicketKind], Awaitable[None]]
_DeliveryHandler = Callable[[discord.Interaction, DeliveryMethod], Awaitable[None]]
_ButtonHandler = Callable[[discord.Interaction], Awaitable[None]]


class TicketPanelView(discord.ui.View):
    """The panel's persistent `📝 Заполнить заявку` button, one instance per `TicketKind`."""

    def __init__(self, kind: TicketKind, on_start: _StartHandler) -> None:
        """Build the view.

        Args:
            kind: Which ticket category this panel is for — baked into the
                button's `custom_id` so a restart-surviving click still
                knows which form to open.
            on_start: Called with the interaction and `kind` on click.
        """
        super().__init__(timeout=None)
        self.add_item(_StartButton(kind, on_start))


class _StartButton(discord.ui.Button["TicketPanelView"]):
    def __init__(self, kind: TicketKind, on_start: _StartHandler) -> None:
        super().__init__(
            label="📝 Заполнить заявку",
            style=discord.ButtonStyle.primary,
            custom_id=f"ticket:start:{kind.value}",
        )
        self._kind = kind
        self._on_start = on_start

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_start(interaction, self._kind)


class DeliveryMethodView(discord.ui.View):
    """Ephemeral `📬 Почта` / `🤝 Обмен` picker shown right after the panel button.

    Not persistent — it lives inside one ephemeral response and is only
    ever meaningful for the few seconds until the player picks an option;
    losing it across a restart just means re-clicking the panel button.
    """

    def __init__(self, on_select: _DeliveryHandler) -> None:
        """Build the view.

        Args:
            on_select: Called with the interaction and the chosen method.
        """
        super().__init__(timeout=180)
        self.add_item(_DeliveryMethodSelect(on_select))


class _DeliveryMethodSelect(discord.ui.Select["DeliveryMethodView"]):
    def __init__(self, on_select: _DeliveryHandler) -> None:
        super().__init__(
            placeholder="Выберите способ передачи",
            options=[
                discord.SelectOption(label="📬 Почта", value=DeliveryMethod.MAIL.value),
                discord.SelectOption(label="🤝 Обмен", value=DeliveryMethod.TRADE.value),
            ],
        )
        self._on_select = on_select

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_select(interaction, DeliveryMethod(self.values[0]))


class TicketSummaryView(discord.ui.View):
    """The summary card's persistent `📸`/`✅` buttons.

    A single shared `custom_id` per button across every ticket channel —
    the handler reads `interaction.channel_id` to know which ticket it's
    acting on, so one registered instance covers every card.
    """

    def __init__(self, on_screenshot: _ButtonHandler, on_confirm: _ButtonHandler) -> None:
        """Build the view.

        Args:
            on_screenshot: Called when `📸 Прикрепить скриншот` is clicked.
            on_confirm: Called when `✅ Подтвердить` is clicked.
        """
        super().__init__(timeout=None)
        self.add_item(_ScreenshotButton(on_screenshot))
        self.add_item(_ConfirmButton(on_confirm))


class _ScreenshotButton(discord.ui.Button["TicketSummaryView"]):
    def __init__(self, on_screenshot: _ButtonHandler) -> None:
        super().__init__(
            label="📸 Прикрепить скриншот",
            style=discord.ButtonStyle.secondary,
            custom_id="ticket:screenshot",
        )
        self._on_screenshot = on_screenshot

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_screenshot(interaction)


class _ConfirmButton(discord.ui.Button["TicketSummaryView"]):
    def __init__(self, on_confirm: _ButtonHandler) -> None:
        super().__init__(
            label="✅ Подтвердить", style=discord.ButtonStyle.success, custom_id="ticket:confirm"
        )
        self._on_confirm = on_confirm

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_confirm(interaction)
