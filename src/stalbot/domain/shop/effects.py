"""What each shop effect means, and how its value is read (заявка 13.09.2026 п.2).

`shop_items.effect_kind` is free text on purpose — the owner invents a new
perk between deploys, and a CHECK constraint would make each one a
migration. This module is the other half of that trade: the list of kinds
the bot knows how to apply *by itself*, and the rules for reading their
`effect_value`.

A kind that is not listed here still sells, still attaches to the player,
and still shows up in «Мои покупки» and in the admin's view of a ticket —
it is simply applied by hand. That is the honest fallback, and it is what
keeps an unknown kind from being either rejected or silently ignored.

Pure: no I/O, no database, no Discord.
"""

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Final


class EffectKind(StrEnum):
    """The effects the bot applies without anyone intervening."""

    DISCOUNT_PERCENT = "discount_percent"
    """Скидка на заказ бустов."""

    MARKUP_PERCENT = "markup_percent"
    """Наценка при сдаче ресурсов."""

    BOTH_PERCENT = "both_percent"
    """Обе стороны сразу («Золотой абонемент»): JSON `{"discount": 3, "markup": 1.5}`."""  # noqa: RUF001

    XP_GRANT = "xp_grant"
    """Разовое начисление XP («Сухой паёк»)."""

    COINS_GRANT = "coins_grant"
    """Разовый возврат Coins. `effect_value` is `"50-150"` for a range, or a number."""

    XP_MULTIPLIER = "xp_multiplier"
    """Множитель XP со сделок («Торговая гильдия»): `"50"` = +50%."""  # noqa: RUF001

    QUEUE_SKIP = "queue_skip"
    """Проход без очереди. Очередь — порядок тикетов от старого к новому."""

    RESERVE_EXTEND = "reserve_extend"
    """Продление брони, в часах."""

    HERE_PING = "here_ping"
    """Право тегать @here; `effect_value` — кулдаун в днях."""

    PROMO_CODE = "promo_code"
    """Личный промокод — заводится через купоны."""

    MANUAL = "manual"
    """Всё остальное: бот хранит и показывает, применяет админ."""


#: Kinds whose `effect_value` is a single percent.
PERCENT_KINDS: Final = frozenset(
    {EffectKind.DISCOUNT_PERCENT, EffectKind.MARKUP_PERCENT, EffectKind.XP_MULTIPLIER}
)

#: Kinds the bot resolves on its own. Everything else is shown to the admin.
AUTOMATIC_KINDS: Final = frozenset(EffectKind) - {EffectKind.MANUAL}

#: How each kind reads in Russian, for the admin's own lists and pickers.
KIND_LABELS: Final[dict[str, str]] = {
    EffectKind.DISCOUNT_PERCENT: "📉 Скидка на заказ, %",
    EffectKind.MARKUP_PERCENT: "📈 Наценка при сдаче, %",
    EffectKind.BOTH_PERCENT: "💎 Скидка и наценка сразу",
    EffectKind.XP_GRANT: "⚡ Разовые XP",
    EffectKind.COINS_GRANT: "🪙 Разовые Coins",
    EffectKind.XP_MULTIPLIER: "🚀 Множитель XP со сделок, %",  # noqa: RUF001
    EffectKind.QUEUE_SKIP: "⏱️ Без очереди",
    EffectKind.RESERVE_EXTEND: "🛡️ Продление брони, ч",
    EffectKind.HERE_PING: "📢 Право на @here",
    EffectKind.PROMO_CODE: "🏷️ Личный промокод",
    EffectKind.MANUAL: "📝 Вручную",
}


@dataclass(frozen=True, slots=True)
class BothPercent:
    """The two-sided percent of «Золотой абонемент»."""

    discount: Decimal
    markup: Decimal


def parse_percent(raw: str | None) -> Decimal | None:
    """Read a percent value, accepting a comma decimal separator.

    Args:
        raw: `effect_value` as stored.

    Returns:
        The percent, or `None` if it is missing or unreadable — an effect
        with an unreadable value must not silently apply as zero.
    """
    if raw is None:
        return None
    try:
        return Decimal(raw.replace(",", ".").strip())
    except (InvalidOperation, AttributeError):
        return None


def parse_both_percent(raw: str | None) -> BothPercent | None:
    """Read `{"discount": 3, "markup": 1.5}`.

    Args:
        raw: `effect_value` as stored.

    Returns:
        Both percents, or `None` if the JSON is missing or malformed.
    """
    if not raw:
        return None
    try:
        document = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(document, dict):
        return None
    discount = parse_percent(str(document.get("discount", "")))
    markup = parse_percent(str(document.get("markup", "")))
    if discount is None or markup is None:
        return None
    return BothPercent(discount=discount, markup=markup)


def parse_amount_range(raw: str | None) -> tuple[int, int] | None:
    """Read `"50-150"` (a range) or `"75"` (a fixed amount).

    Args:
        raw: `effect_value` as stored.

    Returns:
        `(low, high)` — equal for a fixed amount — or `None` if unreadable.
    """
    if not raw:
        return None
    text = raw.strip()
    if "-" in text[1:]:  # not a leading minus sign
        low_text, _, high_text = text.partition("-")
        try:
            low, high = int(low_text.strip()), int(high_text.strip())
        except ValueError:
            return None
        return (low, high) if low <= high else (high, low)
    try:
        value = int(text)
    except ValueError:
        return None
    return value, value


def parse_int(raw: str | None) -> int | None:
    """Read a whole number (hours, days).

    Args:
        raw: `effect_value` as stored.
    """
    if not raw:
        return None
    try:
        return int(raw.strip())
    except ValueError:
        return None


def describe_effect(kind: str, value: str | None) -> str:
    """One human line describing what an effect does.

    Args:
        kind: `shop_items.effect_kind`.
        value: `shop_items.effect_value`.
    """
    label = KIND_LABELS.get(kind, f"❓ {kind}")
    if value is None:
        return label
    return f"{label}: {value}"


def is_automatic(kind: str) -> bool:
    """Whether the bot applies this kind by itself.

    Args:
        kind: `shop_items.effect_kind`.
    """
    return kind in AUTOMATIC_KINDS
