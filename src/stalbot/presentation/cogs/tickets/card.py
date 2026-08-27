"""Builds the one ticket summary card embed from `TicketSession` state (PLAN.md §11.5).

The project invariant this module exists to satisfy: the card is *always*
built by `render_ticket_card(session)` from persisted state, never
assembled ad hoc in a handler — so adding the OCR block in M13 means
editing this one function, not every place a card gets sent or edited.
Empty fields (no referrer yet, no screenshot yet) are simply omitted.
"""

from decimal import Decimal

import discord

from stalbot.application.dto.ticket_session import TicketSession
from stalbot.domain.clock import format_datetime
from stalbot.domain.enums import CouponKind, DeliveryMethod
from stalbot.domain.progression.ranks import RankTier
from stalbot.presentation.embeds.factory import EmbedFactory

#: Filename every screenshot is re-uploaded under (PLAN.md §11.5) — the
#: card's embed always points at `attachment://SCREENSHOT_FILENAME`, which
#: keeps resolving across later edits as long as that attachment stays on
#: the message (Discord does not require the file to be re-sent on an edit
#: that otherwise leaves `attachments` untouched).
SCREENSHOT_FILENAME = "screenshot.png"

_SEPARATOR = "━━━━━━━━━━━━━━━━━━━━━"

#: Shared with `order_card.py` — one delivery-method vocabulary everywhere
#: a ticket shows how the player will send/receive things.
DELIVERY_LABELS: dict[DeliveryMethod, str] = {
    DeliveryMethod.MAIL: "📬 Почта",
    DeliveryMethod.TRADE: "🤝 Обмен",
}


def role_mention(tier: RankTier) -> str:
    """A rank as a Discord role tag, `<@&role_id>` — same convention as tagging a user."""
    return f"<@&{tier.role_id}>"


def format_percent(value: Decimal) -> str:
    """A percent value with no trailing zeros: `Decimal("5.00")` -> `"5"`, `"1.50"` -> `"1.5"`."""
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def format_role_markup(tier: RankTier, multiplier: Decimal) -> str:
    """`"🏷️ Наценка/скидка по рангу <@&...>: -5%"` — заявка 27.08.2026 п.5: percent, not `×0.95`.

    Args:
        tier: The rank whose multiplier this is.
        multiplier: `< 1` is a discount, `> 1` a markup.
    """
    percent = (Decimal(1) - multiplier) * 100
    sign = "-" if percent >= 0 else "+"
    return f"🏷️ Наценка/скидка по рангу {role_mention(tier)}: {sign}{format_percent(abs(percent))}%"


def format_coupon_line(code: str, kind: CouponKind, discount_percent: Decimal, *, verb: str) -> str:
    """`"🎟️ {verb} «CODE»: -5%"` (discount) or `"... +5%"` (markup).

    Args:
        code: The coupon's code.
        kind: Discount or markup.
        discount_percent: Always positive — the sign comes from `kind`.
        verb: E.g. "Промокод" or "Применён промокод".
    """
    sign = "-" if kind is CouponKind.DISCOUNT else "+"
    return f"🎟️ {verb} «{code}»: {sign}{format_percent(discount_percent)}%"


def render_ticket_card(session: TicketSession, embeds: EmbedFactory) -> discord.Embed:
    """Build the public ticket summary card from `session`'s current state.

    Args:
        session: The ticket's persisted state.
        embeds: Factory used to build the underlying `discord.Embed`.

    Returns:
        The card embed. If `session.screenshot_message_id` is set, the
        embed's image points at `attachment://screenshot.png` — the caller
        is responsible for that attachment actually being present on the
        message it sends/edits this embed onto.
    """
    details: list[str] = []
    if session.game_nick:
        details.append(f"🎮 Игровой ник: {session.game_nick}")
    if session.delivery_method is not None:
        details.append(f"📮 Способ: {DELIVERY_LABELS[session.delivery_method]}")
    if session.referrer_nick:
        referrer = session.referrer_nick
        if session.referrer_discord_id is not None:
            referrer += f" (<@{session.referrer_discord_id}>)"
        details.append(f"🤝 Пригласил: {referrer}")
    if session.coupon_code is not None and session.coupon_kind is not None:
        assert session.coupon_discount_percent is not None  # noqa: S101 - set together
        details.append(
            format_coupon_line(
                session.coupon_code,
                session.coupon_kind,
                session.coupon_discount_percent,
                verb="Промокод",
            )
        )

    lines = [_SEPARATOR, f"👤 Игрок: <@{session.author_id}>", "", *details, ""]
    lines.append(f"🕒 Создана: {format_datetime(session.created_at)}")

    embed = embeds.ticket(session.kind, "\n".join(lines))
    if session.screenshot_message_id is not None:
        embed.set_image(url=f"attachment://{SCREENSHOT_FILENAME}")
    return embed
