"""Shared base for every `discord.ui.View` whose buttons can fail (заявка 13.09.2026 п.2).

Mirrors `ErrorReportingModal` (SEC-4): a `discord.ui.View`'s button/select
callbacks dispatch failures through `View.on_error` — a separate pathway
from both `app_commands.CommandTree.on_error` and `Modal.on_error`. Without
an override, discord.py's default `on_error` just logs and returns, so the
player sees the "thinking…" indicator hang with no explanation. The shop's
"Купить" button is the first view whose callback runs a real write
(`ShopService.buy()`) that can raise a `DomainError` the user needs to see.
"""

from typing import Any

import discord

from stalbot.presentation.embeds.factory import EmbedFactory
from stalbot.presentation.errors import on_modal_error
from stalbot.presentation.views.base import AuthorLockedView


class ErrorReportingView(AuthorLockedView):
    """An `AuthorLockedView` whose button/select failures reach the user."""

    def __init__(self, *, author_id: int, embeds: EmbedFactory, timeout: float = 180.0) -> None:
        """Build the view.

        Args:
            author_id: The only Discord user id allowed to interact.
            embeds: Factory used to build the error embed shown on failure.
            timeout: Seconds before the view disables itself unanswered.
        """
        super().__init__(author_id=author_id, timeout=timeout)
        self._embeds = embeds

    async def on_error(
        self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[Any], /
    ) -> None:
        """Route a button/select-callback exception through the project's error-embed convention."""
        await on_modal_error(interaction, error, embeds=self._embeds)
