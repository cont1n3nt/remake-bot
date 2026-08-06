"""Tests for `stalbot.presentation.cogs.tickets.modals` (PLAN.md §11.4)."""

from unittest.mock import AsyncMock

from stalbot.presentation.cogs.tickets.modals import OrderBoostsFormModal
from stalbot.presentation.embeds.factory import EmbedFactory


def test_deadline_field_shows_pending_text_when_reopened_without_an_error() -> None:
    modal = OrderBoostsFormModal(
        AsyncMock(),
        embeds=EmbedFactory(),
        nick="Scaryyyyy",
        deadline_text="через 3 часа",
        error_hint=None,
    )
    assert modal.deadline.default == "через 3 часа"


def test_deadline_field_drops_the_default_so_the_error_placeholder_is_visible() -> None:
    """TICK-4: Discord only shows `placeholder` on an empty field — `default` would hide it."""
    modal = OrderBoostsFormModal(
        AsyncMock(),
        embeds=EmbedFactory(),
        nick="Scaryyyyy",
        deadline_text="через много часов",
        error_hint="Слишком большое число часов.",
    )

    assert modal.deadline.default is None
    assert modal.deadline.placeholder == "Слишком большое число часов."


def test_other_fields_still_carry_their_pending_text_when_reopened_on_error() -> None:
    """Only the deadline field's `default` is dropped — everything else round-trips."""
    modal = OrderBoostsFormModal(
        AsyncMock(),
        embeds=EmbedFactory(),
        nick="Scaryyyyy",
        deadline_text="через много часов",
        referrer_nick="OtherNick",
        referrer_discord_text="<@888>",
        error_hint="Слишком большое число часов.",
    )

    assert modal.nick.default == "Scaryyyyy"
    assert modal.referrer_nick.default == "OtherNick"
    assert modal.referrer_discord.default == "<@888>"
