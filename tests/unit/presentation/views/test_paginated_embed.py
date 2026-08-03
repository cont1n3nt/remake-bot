"""Tests for `stalbot.presentation.views.paginated_embed.PaginatedEmbedView`."""

from unittest.mock import AsyncMock, MagicMock

import discord

from stalbot.presentation.views.paginated_embed import PaginatedEmbedView


def _pages(count: int) -> list[discord.Embed]:
    return [discord.Embed(title=f"page {i}") for i in range(count)]


def _interaction(user_id: int) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock(id=user_id)
    interaction.response = MagicMock()
    interaction.response.edit_message = AsyncMock()
    interaction.response.send_message = AsyncMock()
    return interaction


def test_starts_on_the_first_page_with_previous_disabled() -> None:
    view = PaginatedEmbedView(pages=_pages(3), author_id=1)

    assert view.current.title == "page 0"
    assert view.previous_page.disabled is True
    assert view.next_page.disabled is False


async def test_next_page_advances_and_re_renders() -> None:
    view = PaginatedEmbedView(pages=_pages(3), author_id=1)
    interaction = _interaction(1)

    await view.next_page.callback(interaction)

    assert view.current.title == "page 1"
    interaction.response.edit_message.assert_awaited_once_with(embed=view.current, view=view)


async def test_next_page_disables_itself_on_the_last_page() -> None:
    view = PaginatedEmbedView(pages=_pages(2), author_id=1)
    interaction = _interaction(1)

    await view.next_page.callback(interaction)

    assert view.current.title == "page 1"
    assert view.next_page.disabled is True
    assert view.previous_page.disabled is False


async def test_previous_page_retreats_and_re_renders() -> None:
    view = PaginatedEmbedView(pages=_pages(3), author_id=1)
    view._index = 2  # jump straight to the last page for this test

    interaction = _interaction(1)
    await view.previous_page.callback(interaction)

    assert view.current.title == "page 1"


async def test_previous_page_stays_put_at_the_first_page() -> None:
    view = PaginatedEmbedView(pages=_pages(3), author_id=1)
    interaction = _interaction(1)

    await view.previous_page.callback(interaction)

    assert view.current.title == "page 0"
    assert view.previous_page.disabled is True


async def test_interaction_check_allows_the_author() -> None:
    view = PaginatedEmbedView(pages=_pages(2), author_id=1)
    assert await view.interaction_check(_interaction(1)) is True


async def test_interaction_check_rejects_other_users() -> None:
    view = PaginatedEmbedView(pages=_pages(2), author_id=1)
    interaction = _interaction(999)

    allowed = await view.interaction_check(interaction)

    assert allowed is False
    interaction.response.send_message.assert_awaited_once()


async def test_on_timeout_disables_buttons_and_edits_the_stored_message() -> None:
    view = PaginatedEmbedView(pages=_pages(2), author_id=1)
    message = MagicMock(spec=discord.Message)
    message.edit = AsyncMock()
    view.message = message

    await view.on_timeout()

    assert all(item.disabled for item in view.children if isinstance(item, discord.ui.Button))
    message.edit.assert_awaited_once_with(view=view)


async def test_on_timeout_is_a_no_op_without_a_stored_message() -> None:
    view = PaginatedEmbedView(pages=_pages(2), author_id=1)
    await view.on_timeout()  # must not raise
