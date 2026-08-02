"""Tests for `stalbot.infrastructure.discord.audit_channel` (PLAN.md §5.4)."""

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from stalbot.infrastructure.discord.audit_channel import AuditChannelGateway


def _messageable_channel() -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock()
    return channel


async def test_uses_cached_channel_when_available() -> None:
    channel = _messageable_channel()
    client = MagicMock(spec=discord.Client)
    client.get_channel.return_value = channel
    client.fetch_channel = AsyncMock()

    gateway = AuditChannelGateway(client, 123)
    embeds = [discord.Embed(title="one"), discord.Embed(title="two")]
    await gateway.send_batch(embeds)

    client.get_channel.assert_called_once_with(123)
    client.fetch_channel.assert_not_called()
    channel.send.assert_awaited_once_with(embeds=embeds)


async def test_falls_back_to_fetch_when_not_cached() -> None:
    channel = _messageable_channel()
    client = MagicMock(spec=discord.Client)
    client.get_channel.return_value = None
    client.fetch_channel = AsyncMock(return_value=channel)

    gateway = AuditChannelGateway(client, 123)
    await gateway.send_batch([discord.Embed(title="one")])

    client.fetch_channel.assert_awaited_once_with(123)
    channel.send.assert_awaited_once()


async def test_raises_when_channel_is_not_messageable() -> None:
    client = MagicMock(spec=discord.Client)
    client.get_channel.return_value = MagicMock(spec=discord.CategoryChannel)
    client.fetch_channel = AsyncMock()

    gateway = AuditChannelGateway(client, 123)
    with pytest.raises(RuntimeError, match="not messageable"):
        await gateway.send_batch([discord.Embed(title="one")])
