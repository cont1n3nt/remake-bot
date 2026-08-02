"""`AuditGateway` backed by the configured Discord log channel (PLAN.md §5.4)."""

from collections.abc import Sequence

import discord


class AuditChannelGateway:
    """Sends audit embeds to `Settings.log_channel_id`.

    The channel is resolved on every call rather than once at construction,
    so a transient cache miss (e.g. right after startup) is retried instead
    of permanently breaking audit delivery for the rest of the process.
    """

    def __init__(self, client: discord.Client, channel_id: int) -> None:
        """Create the gateway.

        Args:
            client: The running bot, used to resolve the channel.
            channel_id: `Settings.log_channel_id`.
        """
        self._client = client
        self._channel_id = channel_id

    async def send_batch(self, embeds: Sequence[discord.Embed]) -> None:
        """Send *embeds* (up to 10) as one message to the log channel.

        Raises:
            RuntimeError: If the channel id does not resolve to a
                messageable channel.
        """
        channel = self._client.get_channel(self._channel_id)
        if channel is None:
            channel = await self._client.fetch_channel(self._channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            raise RuntimeError(f"log channel {self._channel_id} is not messageable")
        await channel.send(embeds=list(embeds))
