"""Views for the boost-order editor (PLAN.md §11.6, §11.7).

`OrderEditorView` is persistent (fixed `custom_id`s, `order:select`,
`order:qty:plus`, ...) — one shared registered instance routes every
order-boosts channel's clicks, so handlers must read which line is "active"
from persisted state (`TicketSession.active_order_item_id`), never from the
View instance itself. `BoostMultiSelectView` is the opposite: a short-lived
ephemeral picker, not persistent, so it *can* hold its running selection in
memory.
"""

from collections.abc import Awaitable, Callable, Mapping, Sequence

import discord

from stalbot.application.services.boost_orders import MAX_ORDER_LINES
from stalbot.domain.entities.item import Item
from stalbot.presentation.embeds.factory import EmbedFactory
from stalbot.presentation.views.base import AuthorLockedView

_ButtonHandler = Callable[[discord.Interaction], Awaitable[None]]
_SelectHandler = Callable[[discord.Interaction, int], Awaitable[None]]
_PageChangeHandler = Callable[
    [discord.Interaction, Sequence[Item], frozenset[int]], Awaitable[frozenset[int]]
]

_PAGE_SIZE = 25
_SELECT_LABEL_MAX = 100
_NO_LINES_OPTION = discord.SelectOption(label="Пока ничего не выбрано", value="none")
_NO_BOOSTS_OPTION = discord.SelectOption(label="Нет бустов", value="none")


def _option_label(item: Item, quantity: int | None) -> str:
    """`"🚀 Топот"`, or `"🚀 Топот — 3 шт."` once it's in the draft (PLAN.md §11.6)."""
    label = item.name if quantity is None else f"{item.name} — {quantity} шт."
    return label[:_SELECT_LABEL_MAX]


class OrderEditorView(discord.ui.View):
    """The order editor's persistent controls: line picker + quantity/add/confirm buttons."""

    def __init__(
        self,
        options: Sequence[discord.SelectOption],
        *,
        on_select: _SelectHandler,
        on_plus: _ButtonHandler,
        on_minus: _ButtonHandler,
        on_input_qty: _ButtonHandler,
        on_delete: _ButtonHandler,
        on_add: _ButtonHandler,
        on_confirm: _ButtonHandler,
    ) -> None:
        """Build the view.

        Args:
            options: Select options for the current draft lines (or a
                single disabled placeholder if the draft is empty).
            on_select: Called with the interaction and the chosen item id.
            on_plus: `➕` — increment the active line's quantity.
            on_minus: `➖` — decrement the active line's quantity.
            on_input_qty: `🔢 Ввести количество` — opens the quantity modal.
            on_delete: `🗑️ Удалить` — removes the active line.
            on_add: `➕ Добавить бусты` — opens the multiselect picker.
            on_confirm: `✅ Подтвердить` — any participant (author or admin,
                enforced by the handler, not the view); returns to the
                read-only summary embed (`OrderSummaryView`), UX #1.
        """
        super().__init__(timeout=None)
        self.add_item(_LineSelect(options, on_select))
        self.add_item(_Button("➖", "order:qty:minus", on_minus, row=1))
        self.add_item(_Button("➕", "order:qty:plus", on_plus, row=1))
        self.add_item(_Button("🔢 Ввести количество", "order:qty:input", on_input_qty, row=1))
        self.add_item(
            _Button(
                "🗑️ Удалить",
                "order:qty:delete",
                on_delete,
                row=1,
                style=discord.ButtonStyle.danger,
            )
        )
        self.add_item(_Button("➕ Добавить бусты", "order:add", on_add, row=2))
        self.add_item(
            _Button(
                "✅ Подтвердить",
                "order:confirm",
                on_confirm,
                row=2,
                style=discord.ButtonStyle.success,
            )
        )


class OrderSummaryView(discord.ui.View):
    """The read-only order summary's persistent controls (PLAN.md §11.6, UX #1).

    Shown first after the order form is submitted, and again once the
    editor's "✅ Подтвердить" returns to it.
    """

    def __init__(
        self,
        *,
        on_edit: _ButtonHandler,
        on_complete: _ButtonHandler,
    ) -> None:
        """Build the view.

        Args:
            on_edit: `✏️ Редактировать` — any participant (author or
                admin); opens the interactive editor (`OrderEditorView`).
            on_complete: `🏁 Завершить заказ` (admin-only, enforced by the
                handler, not the view) — registers the deal, same
                `AmountModal` flow the editor's confirm used to trigger
                directly.
        """
        super().__init__(timeout=None)
        self.add_item(_Button("✏️ Редактировать", "order:edit", on_edit, row=0))
        self.add_item(
            _Button(
                "🏁 Завершить заказ",
                "order:complete",
                on_complete,
                row=0,
                style=discord.ButtonStyle.success,
            )
        )


class _LineSelect(discord.ui.Select["OrderEditorView"]):
    def __init__(self, options: Sequence[discord.SelectOption], on_select: _SelectHandler) -> None:
        super().__init__(
            placeholder="Выберите буст для изменения количества",
            options=list(options) or [_NO_LINES_OPTION],
            custom_id="order:select",
            row=0,
        )
        self._on_select = on_select

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.values and self.values[0] != "none":
            await self._on_select(interaction, int(self.values[0]))


class _Button(discord.ui.Button["OrderEditorView"]):
    def __init__(
        self,
        label: str,
        custom_id: str,
        handler: _ButtonHandler,
        *,
        row: int,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
    ) -> None:
        super().__init__(label=label, style=style, custom_id=custom_id, row=row)
        self._handler = handler

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._handler(interaction)


class BoostMultiSelectView(AuthorLockedView):
    """Ephemeral, paginated `➕ Добавить бусты` picker (PLAN.md §11.6).

    Not persistent: it lives inside one ephemeral response, locked to the
    player who opened it. Selection state for the *current* page lives on
    this instance (safe — unlike `OrderEditorView`, each instance belongs
    to exactly one interaction chain); cross-page persistence is the
    caller's job (`BoostOrderService.apply_page_selection`, backed by
    `boost_order_lines`), triggered via `on_change` on every page's submit.
    """

    def __init__(
        self,
        items: Sequence[Item],
        selected_ids: frozenset[int],
        *,
        author_id: int,
        embeds: EmbedFactory,
        on_change: _PageChangeHandler,
        quantities: Mapping[int, int] | None = None,
        timeout: float = 180.0,
    ) -> None:
        """Build the picker.

        Args:
            items: The full boost catalog to page through.
            selected_ids: Item ids already in the draft, across all pages —
                used to pre-check this page's options.
            author_id: The only Discord user id allowed to interact.
            embeds: Builds the status embed shown alongside the picker.
            on_change: Called with the interaction, the items shown on the
                page just submitted, and that page's newly-checked ids.
            quantities: `item_id -> quantity` for already-selected lines, so
                a checked option can show e.g. `"🚀 Топот — 3 шт."`
                (PLAN.md §11.6) instead of just its name.
            timeout: Seconds before the view disables itself.
        """
        super().__init__(author_id=author_id, timeout=timeout)
        self._items = items
        self._selected_ids = set(selected_ids)
        self._embeds = embeds
        self._on_change = on_change
        self._quantities = dict(quantities or {})
        self._page = 0

        self._select: discord.ui.Select[BoostMultiSelectView] = discord.ui.Select(
            placeholder="Выберите бусты…"
        )
        self._select.callback = self._handle_change  # type: ignore[method-assign]
        self.add_item(self._select)
        self._sync()

    @property
    def page_count(self) -> int:
        """Total number of pages (at least 1, even for an empty catalog)."""
        return max(1, -(-len(self._items) // _PAGE_SIZE))

    def status_embed(self, rejected_count: int = 0) -> discord.Embed:
        """The "N positions selected" embed shown alongside the picker.

        Args:
            rejected_count: Newly-checked items that were NOT added because
                the draft was already full (TICK-5) — appended as a warning
                line when non-zero.
        """
        lines = [f"Выбрано: {len(self._selected_ids)} позиций."]
        if rejected_count:
            lines.append(
                f"⚠️ Не добавлено: {rejected_count} — в заказе уже максимум "
                f"{MAX_ORDER_LINES} позиций."
            )
        return self._embeds.info("➕ Добавить бусты", "\n".join(lines))

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary, row=1)
    async def previous_page(
        self, interaction: discord.Interaction, button: discord.ui.Button["BoostMultiSelectView"]
    ) -> None:
        """Go back one page."""
        self._page = max(0, self._page - 1)
        self._sync()
        await interaction.response.edit_message(embed=self.status_embed(), view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary, row=1)
    async def next_page(
        self, interaction: discord.Interaction, button: discord.ui.Button["BoostMultiSelectView"]
    ) -> None:
        """Go forward one page."""
        self._page = min(self.page_count - 1, self._page + 1)
        self._sync()
        await interaction.response.edit_message(embed=self.status_embed(), view=self)

    def _current_page_items(self) -> Sequence[Item]:
        start = self._page * _PAGE_SIZE
        return self._items[start : start + _PAGE_SIZE]

    async def _handle_change(self, interaction: discord.Interaction) -> None:
        page_items = self._current_page_items()
        chosen_ids = frozenset(int(value) for value in self._select.values if value != "none")
        page_ids = {item.id for item in page_items}
        self._selected_ids = (self._selected_ids - page_ids) | chosen_ids
        for item_id in page_ids - chosen_ids:
            self._quantities.pop(item_id, None)
        for item_id in chosen_ids:
            self._quantities.setdefault(item_id, 1)
        self._sync()
        # TICK-9: ack this interaction with the optimistic state right away
        # — `_on_change` below can be slow (it posts/edits the separate,
        # public order-editor message), and an un-acked interaction expires
        # after ~3s. Any correction it reports (TICK-5's rejected ids) is
        # applied via a second, follow-up edit instead of blocking the ack.
        await interaction.response.edit_message(embed=self.status_embed(), view=self)
        rejected = await self._on_change(interaction, page_items, chosen_ids)
        if rejected:
            self._selected_ids -= rejected
            for item_id in rejected:
                self._quantities.pop(item_id, None)
            self._sync()
            await interaction.edit_original_response(
                embed=self.status_embed(len(rejected)), view=self
            )

    def _sync(self) -> None:
        page_items = self._current_page_items()
        self._select.options = [
            discord.SelectOption(
                label=_option_label(item, self._quantities.get(item.id)),
                value=str(item.id),
                default=item.id in self._selected_ids,
            )
            for item in page_items
        ] or [_NO_BOOSTS_OPTION]
        self._select.max_values = max(1, len(page_items))
        self._select.min_values = 0
        self._select.disabled = not page_items
        self.previous_page.disabled = self._page == 0
        self.next_page.disabled = self._page >= self.page_count - 1
