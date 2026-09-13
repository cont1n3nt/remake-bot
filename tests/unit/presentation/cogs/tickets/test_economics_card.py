"""Tests for the «💼 Экономика заказа» log embed (заявка 13.09.2026 п.7)."""

from decimal import Decimal

from stalbot.application.services.order_economics import OrderEconomics, OrderLineCost
from stalbot.presentation.cogs.tickets.economics_card import render_order_economics
from stalbot.presentation.embeds.factory import EmbedFactory


def _economics(
    *, revenue: Decimal, priced: list[OrderLineCost], unpriced: list[OrderLineCost] | None = None
) -> OrderEconomics:
    return OrderEconomics(revenue=revenue, priced=tuple(priced), unpriced=tuple(unpriced or []))


def _render(economics: OrderEconomics) -> str:
    embed = render_order_economics(economics, EmbedFactory(), game_nick="Scaryyyyy", channel_id=555)
    return embed.description or ""


def test_shows_revenue_cost_profit_and_margin() -> None:
    text = _render(
        _economics(
            revenue=Decimal(1000),
            priced=[OrderLineCost(name="Уха", quantity=2, unit_cost_kopeks=30_000)],
        )
    )

    assert "Выручка" in text
    assert "Себестоимость" in text
    assert "Чистая прибыль" in text
    assert "40%" in text


def test_names_the_player_and_links_the_ticket() -> None:
    text = _render(
        _economics(
            revenue=Decimal(100),
            priced=[OrderLineCost(name="Уха", quantity=1, unit_cost_kopeks=1000)],
        )
    )

    assert "Scaryyyyy" in text
    assert "<#555>" in text


def test_warns_that_profit_is_overstated_when_a_line_is_unpriced() -> None:
    """The number is a ceiling, and the reader has to be told that."""
    text = _render(
        _economics(
            revenue=Decimal(1000),
            priced=[OrderLineCost(name="Уха", quantity=1, unit_cost_kopeks=10_000)],
            unpriced=[OrderLineCost(name="Морфин", quantity=3, unit_cost_kopeks=None)],
        )
    )

    assert "Не учтено позиций: 1" in text
    assert "ниже показанной" in text
    assert "Морфин × 3 — себестоимость неизвестна" in text


def test_no_warning_when_every_line_is_priced() -> None:
    text = _render(
        _economics(
            revenue=Decimal(1000),
            priced=[OrderLineCost(name="Уха", quantity=1, unit_cost_kopeks=10_000)],
        )
    )

    assert "Не учтено" not in text


def test_truncates_a_very_long_order_and_says_how_many_are_hidden() -> None:
    priced = [
        OrderLineCost(name=f"Предмет {i}", quantity=1, unit_cost_kopeks=100) for i in range(30)
    ]
    text = _render(_economics(revenue=Decimal(1000), priced=priced))

    assert "и ещё 10" in text
