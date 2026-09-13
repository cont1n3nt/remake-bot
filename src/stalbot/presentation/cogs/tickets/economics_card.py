"""The «💼 Экономика заказа» log embed (заявка 13.09.2026 п.7).

Admin-facing and log-channel-only: it shows what the order cost us, which
is not something a player should read off their own ticket.
"""

from decimal import ROUND_HALF_UP, Decimal

import discord

from stalbot.application.services.order_economics import OrderEconomics, OrderLineCost
from stalbot.domain.money import format_amount
from stalbot.presentation.cogs.tickets.card import format_percent
from stalbot.presentation.embeds.factory import EmbedFactory, enforce_limits

_TITLE = "💼 Экономика заказа"
_MAX_LINES = 20


def _percent(value: Decimal) -> str:
    """One decimal place, trailing zero trimmed — "40%", not "40.0%"."""
    return f"{format_percent(value.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP))}%"


def _line_text(line: OrderLineCost) -> str:
    if line.unit_cost_kopeks is None:
        return f" • {line.name} × {line.quantity} — себестоимость неизвестна"
    return f" • {line.name} × {line.quantity} = {format_amount(line.total_cost)}"


def render_order_economics(
    economics: OrderEconomics, embeds: EmbedFactory, *, game_nick: str, channel_id: int
) -> discord.Embed:
    """Build the log embed for one confirmed boost order.

    Args:
        economics: The computed revenue/cost/profit.
        embeds: Factory used to build the underlying `discord.Embed`.
        game_nick: Whose order this was.
        channel_id: The ticket channel, so the entry can be traced back.
    """
    body = [
        f"🎮 Игрок: {game_nick}",
        f"🎫 Тикет: <#{channel_id}>",
        "",
        f"💰 Выручка: {format_amount(economics.revenue)}",
        f"🧾 Себестоимость: {format_amount(economics.cost)}",
        f"📈 Чистая прибыль: {format_amount(economics.profit)}",
    ]
    margin = economics.margin_percent
    if margin is not None:
        body.append(f"📊 Маржа: {_percent(margin)}")

    if economics.unpriced:
        body.append("")
        body.append(
            f"⚠️ Не учтено позиций: {len(economics.unpriced)} — они не связаны с убежкой, "
            "так что настоящая прибыль ниже показанной."
        )

    shown = [*economics.priced, *economics.unpriced][:_MAX_LINES]
    if shown:
        body.append("")
        body.append("Позиции:")
        body.extend(_line_text(line) for line in shown)
        hidden = len(economics.priced) + len(economics.unpriced) - len(shown)
        if hidden > 0:
            body.append(f" … и ещё {hidden}")

    embed = embeds.info(_TITLE, "\n".join(body))
    return enforce_limits(embed)
