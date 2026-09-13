"""The one "сделка зафиксирована" summary, shared by `/add` and the ticket flow.

заявка 13.09.2026 п.4: closing a ticket used to answer with just
`Сумма: X`, while `/add` answered with the full picture — ник, Discord,
начисленные Coins/XP, привязка. The two paths record the exact same deal
through the same `TransactionService`, so they now render it through the
same function; adding a line to the summary means editing this file, not
finding every place a deal gets confirmed.
"""

from collections.abc import Sequence

from stalbot.domain.clock import format_datetime
from stalbot.domain.entities.deal import Deal
from stalbot.domain.enums import DealType
from stalbot.domain.money import format_amount

DEAL_TYPE_LABEL: dict[DealType, str] = {
    DealType.PURCHASE: "🟢 Покупка (у меня)",  # noqa: RUF001
    DealType.SALE: "🟡 Продажа (мне)",
}


def deal_summary_lines(
    deal: Deal,
    *,
    deal_type: DealType,
    nick: str,
    discord_id: int,
    discord_bound: bool,
    referrer_nick: str | None = None,
    extra: Sequence[str] = (),
) -> list[str]:
    """Build the body of the confirmation embed for one recorded deal.

    Args:
        deal: The deal as persisted — `amount`/`coins`/`xp`/`occurred_at`
            are read from here rather than from what the caller typed, so
            the summary always shows what actually landed in the database.
        deal_type: Which side of the trade this was.
        nick: The player's game nick, as typed.
        discord_id: The player's Discord account, rendered as a mention.
        discord_bound: Whether recording this deal also bound that account
            to the nick — worth calling out, it is a side effect nobody
            asked for explicitly.
        referrer_nick: Shown only when there is one.
        extra: Extra lines appended at the end — warnings from `/add`, the
            rank-markup and coupon notes from a ticket.
    """
    lines = [
        f"📌 Тип: {DEAL_TYPE_LABEL[deal_type]}",
        f"👤 Ник: {nick}",
        f"💬 Discord: <@{discord_id}>",
        f"💰 Сумма: {format_amount(deal.amount)}",
        f"🪙 Начислено: {deal.coins} Coins • ⚡ {deal.xp} XP",
    ]
    if referrer_nick:
        lines.append(f"🤝 Реферал: {referrer_nick}")
    lines.append(f"🕒 Дата: {format_datetime(deal.occurred_at)}")
    if discord_bound:
        lines.append("🔗 Discord ID привязан к нику")
    lines.extend(extra)
    return lines
