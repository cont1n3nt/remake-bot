"""A Select dropdown over a paginated item list, with Prev/Next paging.

★ Groundwork for M10 (PLAN.md M6 checklist): the boost-order item picker
needs exactly this — pick one item from a catalog too long to fit in a
single 25-option Discord Select. No M6 command needs it yet; it is built
now so M10 does not have to invent it under deadline.
"""

from collections.abc import Awaitable, Callable, Sequence
from typing import Final

import discord

from stalbot.domain.entities.item import Item

_PAGE_SIZE: Final = 25
_SELECT_LABEL_MAX = 100


class PaginatedItemSelect(discord.ui.View):
    """Pick one item from a catalog slice, paged 25 options at a time."""

    def __init__(
        self,
        items: Sequence[Item],
        *,
        author_id: int,
        on_select: Callable[[discord.Interaction, Item], Awaitable[None]],
        timeout: float = 180.0,
    ) -> None:
        """Build the picker.

        Args:
            items: The full item list to page through.
            author_id: The only Discord user id allowed to interact.
            on_select: Called with the interaction and the chosen `Item`.
            timeout: Seconds before the view disables itself.
        """
        super().__init__(timeout=timeout)
        self._items = items
        self._author_id = author_id
        self._on_select = on_select
        self._page = 0
        #: Set by the caller after sending, so `on_timeout` can disable the
        #: view on the original message — discord.py does not track this.
        self.message: discord.Message | None = None

        self._select: discord.ui.Select[PaginatedItemSelect] = discord.ui.Select(
            placeholder="Выберите предмет…"
        )
        self._select.callback = self._handle_select  # type: ignore[method-assign]
        self.add_item(self._select)
        self._sync()

    @property
    def page_count(self) -> int:
        """Total number of pages (at least 1, even for an empty list)."""
        return max(1, -(-len(self._items) // _PAGE_SIZE))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Reject interactions from anyone but the original author."""
        if interaction.user.id != self._author_id:
            await interaction.response.send_message("Эта кнопка не для вас.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary, row=1)
    async def previous_page(
        self, interaction: discord.Interaction, button: discord.ui.Button["PaginatedItemSelect"]
    ) -> None:
        """Go back one page."""
        self._page = max(0, self._page - 1)
        self._sync()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary, row=1)
    async def next_page(
        self, interaction: discord.Interaction, button: discord.ui.Button["PaginatedItemSelect"]
    ) -> None:
        """Go forward one page."""
        self._page = min(self.page_count - 1, self._page + 1)
        self._sync()
        await interaction.response.edit_message(view=self)

    async def on_timeout(self) -> None:
        """Disable every control on the original message once the view expires."""
        for item in self.children:
            if isinstance(item, discord.ui.Button | discord.ui.Select):
                item.disabled = True
        if self.message is not None:
            await self.message.edit(view=self)

    async def _handle_select(self, interaction: discord.Interaction) -> None:
        item_id = int(self._select.values[0])
        item = next((i for i in self._items if i.id == item_id), None)
        if item is not None:
            await self._on_select(interaction, item)

    def _sync(self) -> None:
        start = self._page * _PAGE_SIZE
        page_items = self._items[start : start + _PAGE_SIZE]
        self._select.options = [
            discord.SelectOption(label=item.name[:_SELECT_LABEL_MAX], value=str(item.id))
            for item in page_items
        ] or [discord.SelectOption(label="Нет предметов", value="none")]
        self._select.disabled = not page_items
        self.previous_page.disabled = self._page == 0
        self.next_page.disabled = self._page >= self.page_count - 1
