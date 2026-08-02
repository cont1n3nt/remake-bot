"""Reusable Confirm/Cancel prompt (PLAN.md §4, §17.2 point 3).

First use: `/add` warning the admin that a nick is already bound to a
different Discord account before overwriting the binding.
"""

from typing import Final

import discord

_DEFAULT_TIMEOUT_SECONDS: Final = 60.0


class ConfirmView(discord.ui.View):
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
        super().__init__(timeout=timeout)
        self._author_id = author_id
        self.confirmed: bool | None = None
        #: Set by the caller after sending, so `on_timeout` can disable the
        #: buttons on the original message — discord.py does not track this.
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Reject button presses from anyone but the original author."""
        if interaction.user.id != self._author_id:
            await interaction.response.send_message("Эта кнопка не для вас.", ephemeral=True)
            return False
        return True

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

    async def on_timeout(self) -> None:
        """Disable the buttons on the original message once the view expires."""
        self._disable_buttons()
        if self.message is not None:
            await self.message.edit(view=self)

    async def _finish(self, interaction: discord.Interaction) -> None:
        self._disable_buttons()
        await interaction.response.edit_message(view=self)
        self.stop()

    def _disable_buttons(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
