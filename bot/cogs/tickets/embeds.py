"""Построение карточки заявки тикета.

Перенесено из bot/cogs/tickets.py без изменений (REFACTORING_PLAN.md,
Этап F.3). `interaction.client.repo` внутри функции сознательно не
трогается в рамках этого этапа — см. карточку F.3 в REFACTORING_PLAN.md."""

import logging

import discord

from bot.services.ocr_service import _fmt
from bot.utils.embeds import resolve_emoji

logger = logging.getLogger("bot")


# ------------------------------------------------------------------ #
#  Единая точка построения карточки заявки (продажа / заказ бустов)  #
#  Используется при первой публикации, после изменения количества    #
#  бустов и после редактирования заявки — чтобы оформление никогда   #
#  не расходилось между тремя местами вызова.                        #
# ------------------------------------------------------------------ #

def _build_request_card_embed(
    interaction: discord.Interaction,
    text_data: dict,
    delivery: str,
    boosts: list[dict],
    total_price: float,
    category: str,
) -> discord.Embed:
    embed = discord.Embed(
        title=f"📋 Новая заявка — {category}",
        colour=discord.Colour.green(),
        timestamp=discord.utils.utcnow(),
    )
    embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)

    nick = text_data.get("game_nick", "").strip()
    if nick:
        embed.add_field(name="Ник в игре", value=nick)

    if delivery:
        embed.add_field(name="Способ", value=f"📮 {delivery}" if delivery == "Почта" else f"🤝 {delivery}")

    deadline = text_data.get("deadline", "").strip()
    if deadline:
        embed.add_field(name="До даты и времени", value=deadline)

    ref_game = text_data.get("referrer_game", "").strip()
    if ref_game:
        embed.add_field(name="Кто пригласил (игра)", value=ref_game)

    ref_discord = text_data.get("referrer_discord", "").strip()
    if ref_discord:
        embed.add_field(name="Кто пригласил (Discord)", value=ref_discord)

    if boosts:
        items_map = {}
        try:
            all_items = interaction.client.repo.get_all_items()
            for it in all_items:
                if it.get("category") == "boost":
                    items_map[it["name"].lower()] = it
        except Exception as e:
            logger.warning("_build_request_card_embed: failed to fetch items for boost lookup: %s", e)
        boost_lines = []
        for b in boosts:
            it = items_map.get(b["name"].lower())
            e = resolve_emoji(it.get("emoji", ""), interaction.guild) if it else ""
            emoji_str = e + " " if e else ""
            qty = b.get("quantity", 1)
            boost_lines.append(f"{emoji_str}{b['name']} | x{qty}")
        embed.add_field(name="Заказанные бусты", value="\n".join(boost_lines), inline=False)
        embed.add_field(name="Общая стоимость", value=f"{_fmt(total_price)} ₽", inline=False)

    # Отдельный блок для почты — без скобок, отдельно от способа получения
    if delivery == "Почта":
        sep = "━" * 24
        if "Заказ" in category:
            mail_text = f"{sep}\n📮 Почта\nНик: {nick}\nОтправляйте деньги сразу на указанную почту.\n{sep}"
        else:
            mail_text = f"{sep}\n📮 Почта\nНик: {nick}\nОтправляйте ресурсы сразу на указанную почту.\n{sep}"
        if embed.description:
            embed.description += f"\n\n{mail_text}"
        else:
            embed.description = mail_text

    embed.set_footer(text="Клондайк Шёпота")
    return embed
