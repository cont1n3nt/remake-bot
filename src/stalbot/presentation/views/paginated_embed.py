"""A minimal Prev/Next pager over a fixed list of embeds (PLAN.md §10.3).

First use: `/referrals`' referral list, which pages at 15 entries. Deliberately
just two buttons — `/logs` (M7) needs jump-to-page too, but that is a
different, heavier control this milestone has no requirement to build yet.
"""

from collections.abc import Sequence
from typing import Final

import discord

from stalbot.presentation.views.base import AuthorLockedView

_DEFAULT_TIMEOUT_SECONDS: Final = 180.0


class PaginatedEmbedView(AuthorLockedView):
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
        super().__init__(author_id=author_id, timeout=timeout)
        self._pages = pages
        self._index = 0
        self._sync_buttons()

    @property
    def current(self) -> discord.Embed:
        """The embed for the current page."""
        return self._pages[self._index]

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

    async def _render(self, interaction: discord.Interaction) -> None:
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.current, view=self)

    def _sync_buttons(self) -> None:
        self.previous_page.disabled = self._index == 0
        self.next_page.disabled = self._index >= len(self._pages) - 1
