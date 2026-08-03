"""A minimal Prev/Next pager over a fixed list of embeds (PLAN.md §10.3).

First use: `/referrals`' referral list, which pages at 15 entries. Deliberately
just two buttons — `/logs` (M7) needs jump-to-page too, but that is a
different, heavier control this milestone has no requirement to build yet.
"""

from collections.abc import Sequence
from typing import Final

import discord

_DEFAULT_TIMEOUT_SECONDS: Final = 180.0


class PaginatedEmbedView(discord.ui.View):
    """Prev/Next pager over pre-built embeds, locked to one author.

    Usage::

        view = PaginatedEmbedView(pages=pages, author_id=interaction.user.id)
        await interaction.followup.send(embed=view.current, view=view, ephemeral=True)
        view.message = await interaction.original_response()
    """

    def __init__(
        self,
        *,
        pages: Sequence[discord.Embed],
        author_id: int,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        """Build the pager.

        Args:
            pages: The embeds to page through, in order. Must be non-empty.
            author_id: The only Discord user id allowed to press a button.
            timeout: Seconds before the view disables itself.
        """
        super().__init__(timeout=timeout)
        self._pages = pages
        self._author_id = author_id
        self._index = 0
        #: Set by the caller after sending, so `on_timeout` can disable the
        #: buttons on the original message — discord.py does not track this.
        self.message: discord.Message | None = None
        self._sync_buttons()

    @property
    def current(self) -> discord.Embed:
        """The embed for the current page."""
        return self._pages[self._index]

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Reject button presses from anyone but the original author."""
        if interaction.user.id != self._author_id:
            await interaction.response.send_message("Эта кнопка не для вас.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def previous_page(
        self, interaction: discord.Interaction, button: discord.ui.Button["PaginatedEmbedView"]
    ) -> None:
        """Go back one page."""
        self._index = max(0, self._index - 1)
        await self._render(interaction)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next_page(
        self, interaction: discord.Interaction, button: discord.ui.Button["PaginatedEmbedView"]
    ) -> None:
        """Go forward one page."""
        self._index = min(len(self._pages) - 1, self._index + 1)
        await self._render(interaction)

    async def on_timeout(self) -> None:
        """Disable the buttons on the original message once the view expires."""
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        if self.message is not None:
            await self.message.edit(view=self)

    async def _render(self, interaction: discord.Interaction) -> None:
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.current, view=self)

    def _sync_buttons(self) -> None:
        self.previous_page.disabled = self._index == 0
        self.next_page.disabled = self._index >= len(self._pages) - 1
