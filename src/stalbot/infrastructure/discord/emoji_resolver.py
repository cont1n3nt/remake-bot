"""Item-emoji name -> render tag, cached from the guild's custom emoji list.

`Item.emoji` stores a bare emoji *name* (the `AF` column, PLAN.md §6.1), not
a render-ready `<:name:id>` tag — the id can change if the emoji is deleted
and re-uploaded, so storing the name is what stays stable. `EmojiResolver`
is the one place that turns a name into something Discord actually renders,
refreshed whenever the guild's emoji list changes (PLAN.md M6 checklist).
"""

from collections.abc import Sequence

import discord


class EmojiResolver:
    """Caches the guild's custom emoji by name for `<:name:id>` lookups."""

    def __init__(self) -> None:
        """Start with an empty cache; populated by the first `refresh()`."""
        self._by_name: dict[str, discord.Emoji] = {}

    def refresh(self, emojis: Sequence[discord.Emoji]) -> None:
        """Rebuild the name -> emoji cache from the guild's current emoji list.

        Called once at startup (after the guild is available) and again on
        every `on_guild_emojis_update`.

        Args:
            emojis: The guild's full current custom emoji list.
        """
        self._by_name = {emoji.name: emoji for emoji in emojis}

    def resolve(self, name: str | None) -> str | None:
        """Render an item's stored emoji name as `<:name:id>`.

        Args:
            name: The emoji name stored on the item (`Item.emoji`), or `None`.

        Returns:
            The render-ready tag, or `None` if *name* is empty or unknown.
        """
        if not name:
            return None
        emoji = self._by_name.get(name)
        return str(emoji) if emoji is not None else None

    def exists(self, name: str) -> bool:
        """Whether *name* matches a custom emoji currently on the guild.

        Args:
            name: An emoji name, as an admin would type it into `/item_add`.
        """
        return name in self._by_name
