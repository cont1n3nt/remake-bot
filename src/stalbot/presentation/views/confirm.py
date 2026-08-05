"""Reusable Confirm/Cancel prompt (PLAN.md §4, §17.2 point 3).

First use: `/add` warning the admin that a nick is already bound to a
different Discord account before overwriting the binding.
"""

from typing import Final

import discord

from stalbot.presentation.views.base import AuthorLockedView

_DEFAULT_TIMEOUT_SECONDS: Final = 60.0


class ConfirmView(AuthorLockedView):
    """A single-use Confirm/Cancel prompt, locked to one author.

    Usage::

        view = ConfirmView(author_id=interaction.user.id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        view.message = await interaction.original_response()
        await view.wait()
        if view.confirmed:
            ...

    `confirmed` is `True`/`False` once the author clicks a button, or stays
    `None` if the view times out with no response.
    """

    def __init__(self, *, author_id: int, timeout: float = _DEFAULT_TIMEOUT_SECONDS) -> None:
        """Build the view.

        Args:
            author_id: The only Discord user id allowed to press a button.
            timeout: Seconds before the view disables itself unanswered.
        """
        super().__init__(author_id=author_id, timeout=timeout)
        self.confirmed: bool | None = None

    @discord.ui.button(label="✅ Подтвердить", style=discord.ButtonStyle.success)
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button["ConfirmView"]
    ) -> None:
        """Record confirmation and disable the buttons."""
        self.confirmed = True
        await self._finish(interaction)

    @discord.ui.button(label="❌ Отмена", style=discord.ButtonStyle.secondary)
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button["ConfirmView"]
    ) -> None:
        """Record cancellation and disable the buttons."""
        self.confirmed = False
        await self._finish(interaction)

    async def _finish(self, interaction: discord.Interaction) -> None:
        self._disable_children()
        await interaction.response.edit_message(view=self)
        self.stop()
