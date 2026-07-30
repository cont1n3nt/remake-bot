"""Построение карточки заявки тикета.

Единая точка сборки карточки (продажа / заказ бустов): используется при первой
публикации, после изменения количества бустов и после редактирования заявки —
чтобы оформление никогда не расходилось между тремя местами вызова.
"""

import logging

import discord

from bot.config.constants import SHOP_MAIL_NICK
from bot.utils.embeds import resolve_emoji
from bot.utils.formatting import format_amount

logger = logging.getLogger("bot")

MANUAL_CALC_TEXT = "Будет посчитано вручную"


def format_boost_lines(boosts: list[dict], items_map: dict, guild: discord.Guild | None) -> list[str]:
    """Строки бустов в едином виде «эмодзи Название | Количество» (пункт 3).

    Используется и в карточке заявки, и в логе — чтобы количество не терялось
    по дороге (пункт 14)."""
    lines = []
    for b in boosts:
        it = items_map.get(b["name"].lower())
        emoji = resolve_emoji(it.get("emoji", ""), guild) if it else ""
        prefix = f"{emoji} " if emoji else ""
        lines.append(f"{prefix}{b['name']} | {b.get('quantity', 1)}")
    return lines


def boost_items_map(repo) -> dict:
    items_map = {}
    try:
        for it in repo.get_all_items():
            if it.get("category") == "boost":
                items_map[it["name"].lower()] = it
    except Exception as e:
        logger.warning("boost_items_map: failed to fetch items for boost lookup: %s", e)
    return items_map


def _boost_items_map(interaction: discord.Interaction) -> dict:
    return boost_items_map(interaction.client.repo)


def build_audit_details(
    repo,
    guild: discord.Guild | None,
    text_data: dict,
    delivery: str,
    boosts: list[dict],
    total_price: float,
    category: str,
) -> dict:
    """Детали для лога заявки — в том же виде, что и карточка.

    Бусты идут с количеством: раньше в лог уходил только ", ".join(имён), и
    количество терялось (пункт 14)."""
    details = {
        "Категория": category,
        "Ник в игре": text_data.get("game_nick", ""),
    }
    if delivery:
        details["Способ"] = delivery
    deadline = text_data.get("deadline", "").strip()
    if deadline:
        details["До даты и времени"] = deadline
    ref_game = text_data.get("referrer_game", "").strip()
    if ref_game:
        details["Пригласил (игра)"] = ref_game
    ref_discord = text_data.get("referrer_discord", "").strip()
    if ref_discord:
        details["Пригласил (Discord)"] = ref_discord
    if boosts:
        lines = format_boost_lines(boosts, boost_items_map(repo), guild)
        details["Бусты"] = "\n" + "\n".join(lines)
        details["Общая стоимость"] = f"{format_amount(total_price)} ₽"
    elif "Заказ" not in category:
        details["Сумма заявки"] = (
            f"{format_amount(total_price)} ₽" if total_price else MANUAL_CALC_TEXT
        )
    return details


def build_audit_details_from_data(repo, guild: discord.Guild | None, data: dict) -> dict:
    """То же, но из сохранённой меты заявки (published_requests.json)."""
    return build_audit_details(
        repo, guild,
        data.get("text_data", {}), data.get("delivery_method", ""),
        data.get("selected_boosts", []), data.get("total_price", 0.0),
        data.get("category", ""),
    )


def build_request_card_embed(
    author: discord.abc.User,
    guild: discord.Guild | None,
    repo,
    text_data: dict,
    delivery: str,
    boosts: list[dict],
    total_price: float,
    category: str,
) -> discord.Embed:
    """Собрать карточку заявки.

    Принимает автора/гильдию/репозиторий явно, а не через Interaction: карточку
    надо перестраивать и при получении скриншота, когда никакого взаимодействия
    нет — есть только сообщение пользователя (пункты 10, 11).
    """
    embed = discord.Embed(
        title=f"📋 Новая заявка — {category}",
        colour=discord.Colour.green(),
        timestamp=discord.utils.utcnow(),
    )
    embed.set_author(name=author.display_name, icon_url=author.display_avatar.url)

    # Порядок полей задан пунктом 4: создатель отдельной строкой сверху, ниже —
    # ник, способ и срок, в самом низу — бусты и денежный блок.
    embed.add_field(name="👤 Создатель", value=author.mention, inline=False)

    nick = text_data.get("game_nick", "").strip()
    if nick:
        embed.add_field(name="🎮 Ник в игре", value=nick, inline=True)

    if delivery:
        # Без пояснения в скобках — блок про почту уехал вниз (пункт 5).
        embed.add_field(
            name="💳 Способ",
            value=f"📮 {delivery}" if delivery == "Почта" else f"🤝 {delivery}",
            inline=True,
        )

    deadline = text_data.get("deadline", "").strip()
    if deadline:
        embed.add_field(name="⏰ До даты и времени", value=deadline, inline=True)

    ref_game = text_data.get("referrer_game", "").strip()
    if ref_game:
        embed.add_field(name="🤝 Кто пригласил (игра)", value=ref_game, inline=True)

    ref_discord = text_data.get("referrer_discord", "").strip()
    if ref_discord:
        embed.add_field(name="💬 Кто пригласил (Discord)", value=ref_discord, inline=True)

    is_order = "Заказ" in category

    if boosts:
        lines = format_boost_lines(boosts, boost_items_map(repo), guild)
        embed.add_field(name="📦 Выбранные бусты", value="\n".join(lines), inline=False)

    # Сумма заявки: для бустов она всегда посчитана, для продажи приходит из OCR
    # и может отсутствовать — тогда прямо об этом и пишем (пункт 11).
    if is_order:
        if boosts:
            embed.add_field(name="💰 Общая стоимость", value=f"{format_amount(total_price)} ₽", inline=False)
    else:
        amount_text = f"{format_amount(total_price)} ₽" if total_price else MANUAL_CALC_TEXT
        embed.add_field(name="💰 Сумма заявки", value=amount_text, inline=False)

    # Блок «Почта» — в самом низу карточки, с постоянным ником магазина.
    if delivery == "Почта":
        what = "деньги" if is_order else "ресурсы"
        embed.add_field(
            name="📮 Почта",
            value=f"Ник: {SHOP_MAIL_NICK}\nОтправляйте на почту сразу {what}.",
            inline=False,
        )

    embed.set_footer(text="Клондайк Шёпота")
    return embed


def _build_request_card_embed(
    interaction: discord.Interaction,
    text_data: dict,
    delivery: str,
    boosts: list[dict],
    total_price: float,
    category: str,
) -> discord.Embed:
    """Адаптер для мест, где есть Interaction (публикация и редактирование)."""
    return build_request_card_embed(
        interaction.user, interaction.guild, interaction.client.repo,
        text_data, delivery, boosts, total_price, category,
    )
