"""Tests for `stalbot.presentation.cogs.tag.TagCog` (PLAN.md §10.13)."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from stalbot.presentation.cogs.tag import TagCog
from stalbot.presentation.embeds.factory import EmbedFactory


def _messageable_channel(name: str = "ticket-0042") -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.name = name
    channel.send = AsyncMock()
    return channel


def _interaction(
    *, channel: MagicMock | None = None, guild_name: str = "Клондайк Шёпота"
) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    interaction.guild_id = 1475147129201627208
    interaction.channel_id = 123456789
    interaction.channel = channel or _messageable_channel()
    interaction.guild = MagicMock(spec=discord.Guild)
    interaction.guild.name = guild_name
    return interaction


def _member(member_id: int = 999) -> MagicMock:
    member = MagicMock(spec=discord.Member)
    member.id = member_id
    member.mention = f"<@{member_id}>"
    member.send = AsyncMock()
    return member


async def _call_tag(cog: TagCog, interaction: MagicMock, member: MagicMock) -> None:
    callback: Any = TagCog.tag.callback
    await callback(cog, interaction, member)


async def test_tag_sends_a_dm_with_the_ticket_link() -> None:
    cog = TagCog(EmbedFactory())
    interaction = _interaction()
    member = _member()

    await _call_tag(cog, interaction, member)

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    member.send.assert_awaited_once()
    kwargs = member.send.call_args.kwargs
    embed = kwargs["embed"]
    assert "ticket-0042" in (embed.description or "")
    assert "Клондайк Шёпота" in (embed.description or "")
    view = kwargs["view"]
    button = view.children[0]
    assert button.style is discord.ButtonStyle.link
    assert button.url == "https://discord.com/channels/1475147129201627208/123456789"

    confirmation = interaction.followup.send.call_args.kwargs["embed"]
    assert "получил" in (confirmation.description or "")


async def test_tag_falls_back_when_dms_are_closed() -> None:
    cog = TagCog(EmbedFactory())
    channel = _messageable_channel()
    interaction = _interaction(channel=channel)
    member = _member()
    member.send = AsyncMock(
        side_effect=discord.Forbidden(MagicMock(status=403), "Cannot send messages to this user")
    )

    await _call_tag(cog, interaction, member)

    channel.send.assert_awaited_once()
    assert channel.send.call_args.kwargs["content"] == member.mention

    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "DM закрыты" in (embed.title or "")
