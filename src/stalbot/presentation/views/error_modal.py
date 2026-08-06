"""Shared base for every `discord.ui.Modal` in the project (SEC-4).

Discord dispatches a Modal's `on_submit` failures through `Modal.on_error`
— a separate pathway from `app_commands.CommandTree.on_error`, which
`presentation/errors.py::on_app_command_error` already handles for slash
commands. Without an override, discord.py's default `on_error` just logs
and returns: the player/admin sees the "thinking…" indicator hang with no
explanation and no trace id. Every Modal should subclass this instead of
`discord.ui.Modal` directly.
"""

import discord

from stalbot.presentation.embeds.factory import EmbedFactory
from stalbot.presentation.errors import on_modal_error


class ErrorReportingModal(discord.ui.Modal):
    """A `discord.ui.Modal` whose `on_submit` failures reach the user."""

    def __init__(self, *, title: str, embeds: EmbedFactory) -> None:
        """Build the modal.

        Args:
            title: Passed straight through to `discord.ui.Modal`.
            embeds: Factory used to build the error embed shown on failure.
        """
        super().__init__(title=title)
        self._embeds = embeds

    # `discord.ui.Modal.on_error` (2 params) already narrows `BaseView.on_error`
    # (3 params, `interaction`/`error`/`item`) — this is discord.py's own
    # documented signature for a Modal override, not a mistake here; mypy's
    # override-check compares against `BaseView` instead of the intermediate
    # `Modal`, flagging every correctly-signatured Modal.on_error override.
    async def on_error(  # type: ignore[override]
        self, interaction: discord.Interaction, error: Exception, /
    ) -> None:
        """Route any `on_submit` exception through the project's one error-embed convention."""
        await on_modal_error(interaction, error, embeds=self._embeds)
