"""Tests for `stalbot.presentation.views.logs_pager.LogsPagerView` (PLAN.md §10.10)."""

from unittest.mock import AsyncMock, MagicMock

import discord

from stalbot.presentation.views.logs_pager import LogsPagerView, _JumpToPageModal


def _pages(count: int) -> list[discord.Embed]:
    return [discord.Embed(title=f"page {i}") for i in range(count)]


def _interaction(user_id: int) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock(id=user_id)
    interaction.response = MagicMock()
    interaction.response.edit_message = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.send_modal = AsyncMock()
    return interaction


def test_starts_on_the_first_page_with_first_and_previous_disabled() -> None:
    view = LogsPagerView(pages=_pages(3), author_id=1)

    assert view.current.title == "page 0"
    assert view.first_page.disabled is True
    assert view.previous_page.disabled is True
    assert view.next_page.disabled is False
    assert view.last_page.disabled is False
    assert view.page_indicator.label == "1/3"


async def test_next_page_advances_and_re_renders() -> None:
    view = LogsPagerView(pages=_pages(3), author_id=1)
    interaction = _interaction(1)

    await view.next_page.callback(interaction)

    assert view.current.title == "page 1"
    assert view.page_indicator.label == "2/3"
    interaction.response.edit_message.assert_awaited_once_with(embed=view.current, view=view)


async def test_last_page_jumps_to_the_end_and_disables_forward_buttons() -> None:
    view = LogsPagerView(pages=_pages(5), author_id=1)
    interaction = _interaction(1)

    await view.last_page.callback(interaction)

    assert view.current.title == "page 4"
    assert view.next_page.disabled is True
    assert view.last_page.disabled is True


async def test_first_page_returns_to_the_start() -> None:
    view = LogsPagerView(pages=_pages(5), author_id=1)
    view._index = 3

    await view.first_page.callback(_interaction(1))

    assert view.current.title == "page 0"
    assert view.first_page.disabled is True


async def test_previous_page_stays_put_at_the_first_page() -> None:
    view = LogsPagerView(pages=_pages(3), author_id=1)
    interaction = _interaction(1)

    await view.previous_page.callback(interaction)

    assert view.current.title == "page 0"


async def test_interaction_check_allows_the_author() -> None:
    view = LogsPagerView(pages=_pages(2), author_id=1)
    assert await view.interaction_check(_interaction(1)) is True


async def test_interaction_check_rejects_other_users() -> None:
    view = LogsPagerView(pages=_pages(2), author_id=1)
    interaction = _interaction(999)

    allowed = await view.interaction_check(interaction)

    assert allowed is False
    interaction.response.send_message.assert_awaited_once()


async def test_jump_button_opens_the_modal() -> None:
    view = LogsPagerView(pages=_pages(5), author_id=1)
    interaction = _interaction(1)

    await view.jump.callback(interaction)

    interaction.response.send_modal.assert_awaited_once()
    modal = interaction.response.send_modal.call_args.args[0]
    assert isinstance(modal, _JumpToPageModal)


async def test_on_timeout_disables_buttons_and_edits_the_stored_message() -> None:
    view = LogsPagerView(pages=_pages(2), author_id=1)
    message = MagicMock(spec=discord.Message)
    message.edit = AsyncMock()
    view.message = message

    await view.on_timeout()

    assert all(item.disabled for item in view.children if isinstance(item, discord.ui.Button))
    message.edit.assert_awaited_once_with(view=view)


async def test_on_timeout_is_a_no_op_without_a_stored_message() -> None:
    view = LogsPagerView(pages=_pages(2), author_id=1)
    await view.on_timeout()  # must not raise


class TestJumpToPageModal:
    async def test_valid_page_jumps(self) -> None:
        view = LogsPagerView(pages=_pages(5), author_id=1)
        modal = _JumpToPageModal(view)
        modal.page._value = "3"
        interaction = _interaction(1)

        await modal.on_submit(interaction)

        assert view.current.title == "page 2"
        interaction.response.edit_message.assert_awaited_once()

    async def test_out_of_range_page_is_rejected(self) -> None:
        view = LogsPagerView(pages=_pages(5), author_id=1)
        modal = _JumpToPageModal(view)
        modal.page._value = "99"
        interaction = _interaction(1)

        await modal.on_submit(interaction)

        assert view.current.title == "page 0"
        interaction.response.send_message.assert_awaited_once()

    async def test_non_numeric_page_is_rejected(self) -> None:
        view = LogsPagerView(pages=_pages(5), author_id=1)
        modal = _JumpToPageModal(view)
        modal.page._value = "abc"
        interaction = _interaction(1)

        await modal.on_submit(interaction)

        assert view.current.title == "page 0"
        interaction.response.send_message.assert_awaited_once()
