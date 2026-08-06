"""Tests for `stalbot.presentation.cogs.tickets.order_card.render_order_editor` (PLAN.md §11.6)."""

from datetime import UTC, datetime
from decimal import Decimal

from stalbot.application.dto.boost_order_line import BoostOrderLine
from stalbot.application.dto.ticket_session import TicketSession
from stalbot.domain.entities.item import Item
from stalbot.domain.enums import ItemCategory, TicketKind, TicketStatus
from stalbot.domain.money import format_amount
from stalbot.presentation.cogs.tickets.order_card import render_order_editor, render_order_summary
from stalbot.presentation.embeds.factory import EmbedFactory


def _session(**overrides: object) -> TicketSession:
    now = datetime(2026, 7, 31, 21, 45, tzinfo=UTC)
    defaults: dict[str, object] = {
        "channel_id": 111,
        "kind": TicketKind.ORDER_BOOSTS,
        "author_id": 222,
        "status": TicketStatus.FILLED,
        "delivery_method": None,
        "game_nick": "Scaryyyyy",
        "referrer_nick": None,
        "referrer_discord_id": None,
        "deadline": None,
        "screenshot_url": None,
        "screenshot_message_id": None,
        "summary_message_id": None,
        "panel_message_id": None,
        "ocr_status": "disabled",
        "ocr_analysis_id": None,
        "idempotency_key": None,
        "created_at": now,
        "updated_at": now,
        "active_order_item_id": None,
    }
    defaults.update(overrides)
    return TicketSession(**defaults)  # type: ignore[arg-type]


def _item(item_id: int, name: str, price_sell: Decimal | None) -> Item:
    return Item(
        id=item_id,
        name=name,
        category=ItemCategory.BOOST,
        price_buy=None,
        price_sell=price_sell,
        emoji=None,
        updated_at=None,
        row=item_id + 2,
    )


def _line(item_id: int, quantity: int) -> BoostOrderLine:
    return BoostOrderLine(
        channel_id=111,
        item_id=item_id,
        item_name_norm="boost",
        category=ItemCategory.BOOST,
        quantity=quantity,
    )


def test_title_is_the_editor_title_not_the_panel_title() -> None:
    embed = render_order_editor(_session(), [], EmbedFactory())
    assert embed.title == "🧾 Редактор заказа"


def test_empty_order_shows_the_empty_hint() -> None:
    embed = render_order_editor(_session(), [], EmbedFactory())
    assert "пуст" in (embed.description or "")
    assert f"Итого: {format_amount(Decimal(0))}" in (embed.description or "")


def test_lines_are_listed_with_subtotals_and_summed() -> None:
    lines_with_items = [
        (_line(1, 3), _item(1, "Топот", Decimal(300000))),
        (_line(2, 1), _item(2, "Ускорение", Decimal(150000))),
    ]

    embed = render_order_editor(_session(), lines_with_items, EmbedFactory())

    description = embed.description or ""
    assert "Топот × 3" in description
    assert "Ускорение × 1" in description
    assert f"Итого: {format_amount(Decimal(1050000))}" in description


def test_a_deleted_item_is_omitted_from_the_list_and_total() -> None:
    lines_with_items = [
        (_line(1, 3), _item(1, "Топот", Decimal(300000))),
        (_line(2, 1), None),
    ]

    embed = render_order_editor(_session(), lines_with_items, EmbedFactory())

    description = embed.description or ""
    assert "Топот" in description
    assert f"Итого: {format_amount(Decimal(900000))}" in description


def test_deadline_is_shown_when_set() -> None:
    deadline = datetime(2026, 8, 2, 20, 0, tzinfo=UTC)
    embed = render_order_editor(_session(deadline=deadline), [], EmbedFactory())

    assert "Срок:" in (embed.description or "")


def test_deadline_is_omitted_when_unset() -> None:
    embed = render_order_editor(_session(deadline=None), [], EmbedFactory())

    assert "Срок:" not in (embed.description or "")


def test_summary_title_differs_from_the_editor_title() -> None:
    """UX #1: the read-only summary and the interactive editor are visually distinct."""
    embed = render_order_summary(_session(), [], EmbedFactory())
    assert embed.title == "🧾 Заказ бустов"
    assert embed.title != render_order_editor(_session(), [], EmbedFactory()).title


def test_summary_shares_the_same_body_as_the_editor() -> None:
    lines_with_items = [(_line(1, 3), _item(1, "Топот", Decimal(300000)))]

    summary = render_order_summary(_session(), lines_with_items, EmbedFactory())
    editor = render_order_editor(_session(), lines_with_items, EmbedFactory())

    assert summary.description == editor.description
