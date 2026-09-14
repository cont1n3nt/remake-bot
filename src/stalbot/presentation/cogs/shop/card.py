"""Rendering the Coins shop for Discord — player storefront and admin views (заявка 13.09.2026 п.2).

Every embed here shares one voice: price first, then what buying it does,
in the language `domain.shop.effects.describe_effect` already speaks —
this module never reinvents that wording, only lays it out.
"""

from collections.abc import Sequence
from typing import Final

import discord

from stalbot.domain.clock import format_datetime
from stalbot.domain.entities.player import Player
from stalbot.domain.entities.shop import PlayerEffect, ShopCategory, ShopItem, ShopPurchase
from stalbot.domain.money import format_amount
from stalbot.domain.shop.effects import describe_effect, is_automatic
from stalbot.presentation.embeds.factory import EmbedFactory, enforce_limits

_STATUS_LABEL: Final[dict[str, str]] = {
    "active": "✅ активна",
    "used": "☑️ использована",
    "expired": "⌛ истекла",
    "refunded": "↩️ возвращена",
}


def _coins(amount: int) -> str:
    return f"{format_amount(amount, currency=False)} Coins"


def _title(item: ShopItem) -> str:
    return f"{item.emoji} {item.name}" if item.emoji else item.name


def _item_meta_lines(item: ShopItem) -> list[str]:
    lines = [f"🎯 Эффект: {describe_effect(item.effect_kind, item.effect_value)}"]
    if item.duration_days is not None:
        lines.append(f"⏳ Действует: {item.duration_days} дн.")
    if item.uses is not None:
        lines.append(f"🔁 Использований: {item.uses}")
    if item.stock is not None:
        lines.append(f"📦 Остаток: {item.stock}")
    if item.per_player_limit is not None:
        lines.append(f"🙋 Лимит на игрока: {item.per_player_limit}")
    if not is_automatic(item.effect_kind):
        lines.append("📝 Применяется вручную администратором.")
    return lines


def render_categories(
    categories: Sequence[ShopCategory], embeds: EmbedFactory, *, balance: int
) -> discord.Embed:
    """The `/shop` home screen: balance plus every category on the shelf."""
    header = f"💳 Баланс: {_coins(balance)}"
    if not categories:
        return embeds.info("🛒 Магазин", f"{header}\n\nМагазин пока пуст.")
    lines = [header, ""]
    for category in categories:
        lines.append(
            f"**{category.name}**" + (f" — {category.description}" if category.description else "")
        )
    lines.append("")
    lines.append("Выберите категорию ниже 👇")
    return enforce_limits(embeds.info("🛒 Магазин", "\n".join(lines)))


def render_items(
    items: Sequence[ShopItem], category: ShopCategory, embeds: EmbedFactory, *, balance: int
) -> discord.Embed:
    """One category's shelf: every item on sale in it, with its price."""
    title = f"🛒 {category.name}"
    header = f"💳 Баланс: {_coins(balance)}"
    if not items:
        return embeds.info(title, f"{header}\n\nВ этой категории пока нет товаров.")
    lines = [header, ""]
    for item in items:
        lines.append(f"{_title(item)} — {_coins(item.price_coins)}")
        lines.append(f"-# {item.description}")
    lines.append("")
    lines.append("Выберите товар ниже 👇")
    return enforce_limits(embeds.info(title, "\n".join(lines)))


def render_item_detail(
    item: ShopItem, embeds: EmbedFactory, *, balance: int, confirming: bool
) -> discord.Embed:
    """One item's full card, plus a purchase-confirmation line once confirming."""
    lines = [
        f"💰 Цена: {_coins(item.price_coins)}",
        f"💳 Баланс: {_coins(balance)}",
        "",
        item.description,
        "",
        *_item_meta_lines(item),
    ]
    if confirming:
        after = balance - item.price_coins
        lines.append("")
        lines.append(f"⚠️ Подтвердите покупку — баланс станет {_coins(after)}.")
    return enforce_limits(embeds.info(_title(item), "\n".join(lines)))


def render_purchase_receipt(
    item: ShopItem, embeds: EmbedFactory, *, balance_before: int, balance_after: int
) -> discord.Embed:
    """The receipt shown once `ShopService.buy()` actually succeeds."""
    lines = [
        _title(item),
        f"💰 Было: {_coins(balance_before)}",
        f"💰 Стало: {_coins(balance_after)}",
        f"🎯 {describe_effect(item.effect_kind, item.effect_value)}",
    ]
    return embeds.success("✅ Покупка совершена", "\n".join(lines))


def render_purchase_history(
    rows: Sequence[tuple[ShopPurchase, ShopItem | None]],
    embeds: EmbedFactory,
    *,
    page: int = 1,
    pages: int = 1,
) -> discord.Embed:
    """One page of «Мои покупки» — newest first, name resolved per row."""
    title = "📜 Мои покупки" if pages == 1 else f"📜 Мои покупки (стр. {page}/{pages})"
    if not rows:
        return embeds.info(title, "Пока нет покупок.")
    lines = []
    for purchase, item in rows:
        name = _title(item) if item is not None else f"#{purchase.shop_item_id}"
        status = _STATUS_LABEL.get(purchase.status, purchase.status)
        when = format_datetime(purchase.purchased_at) if purchase.purchased_at else "—"
        lines.append(f"**{name}** — {_coins(purchase.price_coins)} · {status} · {when}")
    return enforce_limits(embeds.info(title, "\n".join(lines)))


def render_live_effects(effects: Sequence[PlayerEffect], embeds: EmbedFactory) -> discord.Embed:
    """Every perk currently applying — shown alongside «Мои покупки»."""
    if not effects:
        return embeds.info("✨ Активные эффекты", "Сейчас ничего не активно.")
    lines = []
    for effect in effects:
        extra = []
        if effect.uses_left is not None:
            extra.append(f"осталось использований: {effect.uses_left}")
        if effect.expires_at is not None:
            extra.append(f"до {format_datetime(effect.expires_at)}")
        suffix = f" ({', '.join(extra)})" if extra else ""
        lines.append(f" • {describe_effect(effect.effect_kind, effect.effect_value)}{suffix}")
    return enforce_limits(embeds.info("✨ Активные эффекты", "\n".join(lines)))


# -- admin -------------------------------------------------------------------


def render_category_list(categories: Sequence[ShopCategory], embeds: EmbedFactory) -> discord.Embed:
    """`/shop_item categories` — every category, active or not."""
    if not categories:
        return embeds.info("🗂️ Категории", "Категорий нет.")
    lines = []
    for category in categories:
        status = "🟢" if category.active else "🔴"
        line = f"{status} **{category.key}** — {category.name}"
        if category.description:
            line += f": {category.description}"
        lines.append(line)
    return enforce_limits(embeds.info("🗂️ Категории", "\n".join(lines)))


def render_admin_item(item: ShopItem, embeds: EmbedFactory) -> discord.Embed:
    """One item's full card, from the admin's side (id, category key, raw status)."""
    if item.deleted_at is not None:
        status = "🗑️ удалён"
    else:
        status = "🟢 активен" if item.active else "🔴 выключен"
    lines = [
        f"🆔 #{item.id}",
        f"🗂️ Категория: {item.category_key}",
        f"💰 Цена: {_coins(item.price_coins)}",
        f"📋 Статус: {status}",
        item.description,
        "",
        *_item_meta_lines(item),
    ]
    return enforce_limits(embeds.info(_title(item), "\n".join(lines)))


def render_admin_item_list(
    items: Sequence[ShopItem], embeds: EmbedFactory, *, title: str, page: int = 1, pages: int = 1
) -> discord.Embed:
    """One page of `/shop_item list` — every item, active or not."""
    page_title = title if pages == 1 else f"{title} (стр. {page}/{pages})"
    if not items:
        return embeds.info(page_title, "Товаров нет.")
    lines = []
    for item in items:
        status = "🗑️" if item.deleted_at is not None else ("🟢" if item.active else "🔴")
        lines.append(
            f"{status} **#{item.id}** {_title(item)} — {_coins(item.price_coins)} · "
            f"{item.category_key}"
        )
    return enforce_limits(embeds.info(page_title, "\n".join(lines)))


def render_recent_purchases(
    rows: Sequence[tuple[ShopPurchase, ShopItem | None, Player | None]], embeds: EmbedFactory
) -> discord.Embed:
    """`/shop_item recent` — the admin's own picker for `/shop_item refund`."""
    if not rows:
        return embeds.info("🧾 Последние покупки", "Покупок ещё не было.")
    lines = []
    for purchase, item, player in rows:
        name = _title(item) if item is not None else f"#{purchase.shop_item_id}"
        buyer = player.nick_display if player is not None else f"player#{purchase.player_id}"
        status = _STATUS_LABEL.get(purchase.status, purchase.status)
        when = format_datetime(purchase.purchased_at) if purchase.purchased_at else "—"
        lines.append(
            f"**#{purchase.id}** {name} · {buyer} — {_coins(purchase.price_coins)} · "
            f"{status} · {when}"
        )
    return enforce_limits(embeds.info("🧾 Последние покупки", "\n".join(lines)))
