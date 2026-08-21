"""Tests for `stalbot.presentation.cogs.purchase_calculator.PurchaseCalculatorCog` (Э8).

`BoostOrderService` is mocked — its own round-trip behavior is covered by
`tests/unit/application/services/test_boost_orders.py`; this file only
checks the cog wires interactions to it correctly.
"""

from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from stalbot.application.dto.bulk_entry_report import BulkEntryReport
from stalbot.presentation.cogs.purchase_calculator import (
    BulkEntryModal,
    PurchaseCalculatorCog,
)
from stalbot.presentation.embeds.factory import EmbedFactory

_CHANNEL_ID = 111


def _empty_report() -> BulkEntryReport:
    return BulkEntryReport(applied=[], removed=[], not_parsed=[], not_found=[], ambiguous=[])


def _orders(*, lines_with_items: list[Any] | None = None, total: Decimal = Decimal(0)) -> MagicMock:
    orders = MagicMock()
    orders.list_lines_with_items = AsyncMock(return_value=lines_with_items or [])
    orders.compute_total = AsyncMock(return_value=total)
    orders.list_lines = AsyncMock(return_value=[])
    orders.clear = AsyncMock()
    orders.apply_bulk_entry = AsyncMock(return_value=_empty_report())
    return orders


def _cog(orders: MagicMock | None = None) -> PurchaseCalculatorCog:
    return PurchaseCalculatorCog(orders or _orders(), EmbedFactory())


def _interaction() -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.channel_id = _CHANNEL_ID
    interaction.channel = MagicMock(spec=discord.TextChannel)
    interaction.channel.send = AsyncMock()
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.send_modal = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


async def test_skupka_posts_a_card_with_the_persistent_view() -> None:
    cog = _cog()
    interaction = _interaction()

    callback: Any = PurchaseCalculatorCog.skupka.callback
    await callback(cog, interaction)

    interaction.response.send_message.assert_awaited_once()
    kwargs = interaction.response.send_message.call_args.kwargs
    assert kwargs["embed"].title == "🧮 Калькулятор скупки"
    assert kwargs["view"] is not None


async def test_bulk_button_opens_the_modal() -> None:
    cog = _cog()
    interaction = _interaction()

    await cog._on_bulk_button(interaction)

    interaction.response.send_modal.assert_awaited_once()
    modal = interaction.response.send_modal.call_args.args[0]
    assert isinstance(modal, BulkEntryModal)


async def test_bulk_submitted_applies_the_paste_and_posts_a_fresh_card() -> None:
    orders = _orders()
    cog = _cog(orders)
    interaction = _interaction()

    await cog._on_bulk_submitted(interaction, "Кристалл x5")

    orders.apply_bulk_entry.assert_awaited_once_with(_CHANNEL_ID, "Кристалл x5")
    interaction.channel.send.assert_awaited_once()
    interaction.followup.send.assert_awaited_once()


async def test_bulk_submitted_reports_success_when_everything_resolved() -> None:
    cog = _cog()
    interaction = _interaction()

    await cog._on_bulk_submitted(interaction, "Кристалл x5")

    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "обработан" in (embed.title or "")


async def test_bulk_submitted_flags_unresolved_lines() -> None:
    orders = _orders()
    orders.apply_bulk_entry = AsyncMock(
        return_value=BulkEntryReport(
            applied=[],
            removed=[],
            not_parsed=["сломано"],
            not_found=["Несуществующий x2"],
            ambiguous=["Уха x1"],
        )
    )
    cog = _cog(orders)
    interaction = _interaction()

    await cog._on_bulk_submitted(interaction, "irrelevant")

    embed = interaction.followup.send.call_args.kwargs["embed"]
    description = embed.description or ""
    assert "сломано" in description
    assert "Несуществующий x2" in description
    assert "Уха x1" in description


async def test_reset_button_clears_the_channels_draft() -> None:
    orders = _orders()
    orders.list_lines = AsyncMock(return_value=[MagicMock(), MagicMock()])
    cog = _cog(orders)
    interaction = _interaction()

    await cog._on_reset_button(interaction)

    orders.clear.assert_awaited_once_with(_CHANNEL_ID)
    interaction.response.send_message.assert_awaited_once()
    interaction.followup.send.assert_awaited_once()
    confirmation = interaction.followup.send.call_args.kwargs["embed"]
    assert "2" in (confirmation.description or "")
