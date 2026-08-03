"""Tests for `stalbot.infrastructure.discord.emoji_resolver.EmojiResolver`."""

from unittest.mock import MagicMock

from stalbot.infrastructure.discord.emoji_resolver import EmojiResolver


def _emoji(name: str, rendered: str) -> MagicMock:
    emoji = MagicMock()
    emoji.name = name
    emoji.__str__.return_value = rendered  # type: ignore[attr-defined]
    return emoji


def test_resolve_returns_none_before_any_refresh() -> None:
    resolver = EmojiResolver()
    assert resolver.resolve("topot") is None


def test_resolve_returns_none_for_empty_or_missing_name() -> None:
    resolver = EmojiResolver()
    resolver.refresh([_emoji("topot", "<:topot:1>")])
    assert resolver.resolve(None) is None
    assert resolver.resolve("") is None
    assert resolver.resolve("unknown") is None


def test_resolve_renders_a_known_emoji() -> None:
    resolver = EmojiResolver()
    resolver.refresh([_emoji("topot", "<:topot:1>")])
    assert resolver.resolve("topot") == "<:topot:1>"


def test_refresh_replaces_the_previous_cache() -> None:
    resolver = EmojiResolver()
    resolver.refresh([_emoji("topot", "<:topot:1>")])
    resolver.refresh([_emoji("tail", "<:tail:2>")])
    assert resolver.resolve("topot") is None
    assert resolver.resolve("tail") == "<:tail:2>"


def test_exists_reflects_the_cache() -> None:
    resolver = EmojiResolver()
    resolver.refresh([_emoji("topot", "<:topot:1>")])
    assert resolver.exists("topot") is True
    assert resolver.exists("unknown") is False
