"""`/logs`'s pager: `⏮ ◀ n/m ▶ ⏭` plus a jump-to-page modal (PLAN.md §10.10).

Heavier than `PaginatedEmbedView` (`/referrals`' simple Prev/Next) — kept as
its own component so that view's simpler contract, shared by every other
pager in the project, doesn't grow buttons only `/logs` needs.
"""

from collections.abc import Sequence
from typing import Final

import discord

_DEFAULT_TIMEOUT_SECONDS: Final = 300.0


class LogsPagerView(discord.ui.View):
    """`⏮ ◀ n/m ▶ ⏭` + `🔢 К странице`, locked to one author.

    Usage::

        view = LogsPagerView(pages=pages, author_id=interaction.user.id)
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
            author_id: The only Discord user id allowed to interact.
            timeout: Seconds before the view disables itself.
        """
        super().__init__(timeout=timeout)
        self._pages = pages
        self._author_id = author_id
        self._index = 0
        #: Set by the caller after sending, so `on_timeout` can disable the
        #: view on the original message — discord.py does not track this.
        self.message: discord.Message | None = None
        self._sync()

    @property
    def current(self) -> discord.Embed:
        """The embed for the current page."""
        return self._pages[self._index]

    @property
    def page_count(self) -> int:
        """Total number of pages."""
        return len(self._pages)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Reject interactions from anyone but the original author."""
        if interaction.user.id != self._author_id:
            await interaction.response.send_message("Эта кнопка не для вас.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="⏮", style=discord.ButtonStyle.secondary, row=0)
    async def first_page(
        self, interaction: discord.Interaction, button: discord.ui.Button["LogsPagerView"]
    ) -> None:
        """Jump to the first page."""
        await self.go_to_page(interaction, 0)

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary, row=0)
    async def previous_page(
        self, interaction: discord.Interaction, button: discord.ui.Button["LogsPagerView"]
    ) -> None:
        """Go back one page."""
        await self.go_to_page(interaction, max(0, self._index - 1))

    @discord.ui.button(label="1/1", style=discord.ButtonStyle.secondary, disabled=True, row=0)
    async def page_indicator(
        self, interaction: discord.Interaction, button: discord.ui.Button["LogsPagerView"]
    ) -> None:
        """Never actually pressable — `disabled` is always `True`."""

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary, row=0)
    async def next_page(
        self, interaction: discord.Interaction, button: discord.ui.Button["LogsPagerView"]
    ) -> None:
        """Go forward one page."""
        await self.go_to_page(interaction, min(self.page_count - 1, self._index + 1))

    @discord.ui.button(label="⏭", style=discord.ButtonStyle.secondary, row=0)
    async def last_page(
        self, interaction: discord.Interaction, button: discord.ui.Button["LogsPagerView"]
    ) -> None:
        """Jump to the last page."""
        await self.go_to_page(interaction, self.page_count - 1)

    @discord.ui.button(label="🔢 К странице", style=discord.ButtonStyle.primary, row=1)
    async def jump(
        self, interaction: discord.Interaction, button: discord.ui.Button["LogsPagerView"]
    ) -> None:
        """Open the jump-to-page modal."""
        await interaction.response.send_modal(_JumpToPageModal(self))

    async def go_to_page(self, interaction: discord.Interaction, index: int) -> None:
        """Render *index* and edit the original message via *interaction*.

        Public so `_JumpToPageModal.on_submit` (a different interaction than
        the one that opened it) can reuse it.
        """
        self._index = index
        self._sync()
        await interaction.response.edit_message(embed=self.current, view=self)

    async def on_timeout(self) -> None:
        """Disable every control on the original message once the view expires."""
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        if self.message is not None:
            await self.message.edit(view=self)

    def _sync(self) -> None:
        self.first_page.disabled = self._index == 0
        self.previous_page.disabled = self._index == 0
        self.next_page.disabled = self._index >= self.page_count - 1
        self.last_page.disabled = self._index >= self.page_count - 1
        self.page_indicator.label = f"{self._index + 1}/{self.page_count}"


class _JumpToPageModal(discord.ui.Modal):
    """Single-field modal opened by `LogsPagerView`'s `🔢 К странице` button."""

    page = discord.ui.TextInput[discord.ui.Modal](label="Номер страницы", max_length=6)

    def __init__(self, view: LogsPagerView) -> None:
        """Build the modal.

        Args:
            view: The pager to jump within once a valid page is submitted.
        """
        super().__init__(title="Перейти к странице")
        self._view = view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Validate the typed page number and jump to it."""
        raw = str(self.page.value).strip()
        if not raw.isdigit() or not (1 <= int(raw) <= self._view.page_count):
            await interaction.response.send_message(
                f"Введите число от 1 до {self._view.page_count}.", ephemeral=True
            )
            return
        await self._view.go_to_page(interaction, int(raw) - 1)
