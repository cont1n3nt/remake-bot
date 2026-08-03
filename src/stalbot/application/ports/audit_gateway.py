"""Port for delivering already-built audit embeds (PLAN.md §5.4)."""

from collections.abc import Sequence
from typing import Protocol

import discord


class AuditGateway(Protocol):
    """Sends a batch of audit embeds to wherever they are archived."""

    async def send_batch(self, embeds: Sequence[discord.Embed]) -> None:
        """Send up to 10 embeds as a single message.

        Args:
            embeds: The embeds to deliver together, in order.

        Raises:
            Exception: Any delivery failure (a Discord API error, or the
                channel being unavailable) propagates to the caller, which
                falls back to a file log.
        """
        ...
