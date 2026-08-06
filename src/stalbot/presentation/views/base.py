"""Shared base for author-locked, self-disabling Discord UI views.

Every pager/prompt in the project (`ConfirmView`, `PaginatedEmbedView`,
`LogsPagerView`, catalog's `_PriceListView`) needs the same two things:
reject button presses from anyone but the original author, and disable its
own controls on the original message once it times out. This base class is
the one place that pattern is implemented, instead of independent (and
previously independently-buggy) copies.
"""

import logging
from typing import Final

import discord

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS: Final = 180.0


class AuthorLockedView(discord.ui.View):
    """A `discord.ui.View` locked to one author, with a safe timeout handler.

    Subclasses just add their own buttons/selects; `interaction_check` and
    `on_timeout` are inherited as-is unless a subclass has extra state to
    reset (see `ConfirmView`, which layers its own `_finish` on top of
    `_disable_children`).
    """

    def __init__(self, *, author_id: int, timeout: float = _DEFAULT_TIMEOUT_SECONDS) -> None:
        """Build the view.

        Args:
            author_id: The only Discord user id allowed to interact.
            timeout: Seconds before the view disables itself unanswered.
        """
        super().__init__(timeout=timeout)
        self._author_id = author_id
        #: Set by the caller after sending, so `on_timeout` can disable the
        #: view on the original message — discord.py does not track this.
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Reject interactions from anyone but the original author."""
        if interaction.user.id != self._author_id:
            await interaction.response.send_message("Эта кнопка не для вас.", ephemeral=True)
            return False
        return True

    async def on_timeout(self) -> None:
        """Disable every control on the original message once the view expires."""
        self._disable_children()
        if self.message is None:
            return
        try:
            await self.message.edit(view=self)
        except (discord.NotFound, discord.Forbidden):
            # The message was deleted, or the bot lost access to the
            # channel, before the view timed out — nothing left to disable.
            logger.debug("could not disable an expired view: message unavailable", exc_info=True)

    def _disable_children(self) -> None:
        # Covers `Select` too, not just `Button` — none of today's
        # subclasses add one, but a future one that does should not have to
        # rediscover this.
        for item in self.children:
            if isinstance(item, discord.ui.Button | discord.ui.Select):
                item.disabled = True
