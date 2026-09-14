"""`/shop_item` — admin CRUD for the Coins shop assortment (заявка 13.09.2026 п.2).

One command group instead of many top-level commands, same reasoning as
`/recipe` (`recipes/cog.py`): the owner edits this from Discord, not code,
and a new item must not need a migration.
"""

from collections.abc import Sequence
from typing import Final

import discord
from discord import app_commands
from discord.ext import commands

from stalbot.application.services.shop import ShopService
from stalbot.domain.entities.player import Player
from stalbot.domain.entities.shop import ShopItem, ShopPurchase
from stalbot.domain.shop.effects import KIND_LABELS, EffectKind, describe_effect
from stalbot.infrastructure.cache.repositories.players import PlayersRepository
from stalbot.presentation.checks import admin_only
from stalbot.presentation.cogs.shop.card import (
    render_admin_item,
    render_admin_item_list,
    render_category_list,
    render_recent_purchases,
)
from stalbot.presentation.embeds.factory import EmbedFactory
from stalbot.presentation.views.confirm import ConfirmView

_LIST_PAGE_SIZE: Final = 15
_MAX_CHOICES: Final = 25

_EFFECT_CHOICES = [
    app_commands.Choice(name=KIND_LABELS[kind], value=kind.value) for kind in EffectKind
]


class ShopAdminCog(commands.Cog):
    """The `/shop_item` group: categories, items, and refunds."""

    shop_item = app_commands.Group(name="shop_item", description="🛡️ [Админ] 🛍️ Ассортимент магазина")

    def __init__(self, shop: ShopService, players: PlayersRepository, embeds: EmbedFactory) -> None:
        """Wire the cog to the services it delegates to.

        Args:
            shop: All category/item/purchase reads and writes.
            players: Resolves a buyer's nick for `/shop_item recent`/`refund`.
            embeds: Builds every embed this cog sends.
        """
        self._shop = shop
        self._players = players
        self._embeds = embeds

    # -- categories ------------------------------------------------------

    @shop_item.command(name="category", description="🗂️ Добавить или изменить категорию")
    @app_commands.describe(
        ключ="Стабильный идентификатор категории, например small — никогда не виден игроку",
        название="Название, например «🟢 Мелкие товары»",
        описание="Показывается над списком товаров — необязательно",
        порядок="Меньше — выше в списке",
        активна="Показывать ли категорию на витрине",
    )
    @admin_only()
    async def category(
        self,
        interaction: discord.Interaction,
        ключ: str,
        название: str,
        описание: str | None = None,
        порядок: int = 0,
        активна: bool = True,
    ) -> None:
        """Handle `/shop_item category`: create or update a category."""
        await interaction.response.defer(ephemeral=True)
        category = await self._shop.upsert_category(
            ключ, название, description=описание, sort_order=порядок, active=активна
        )
        embed = self._embeds.success(
            "✅ Категория сохранена", f"**{category.name}** (`{category.key}`)"
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @shop_item.command(name="categories", description="🗂️ Список категорий")
    @admin_only()
    async def categories(self, interaction: discord.Interaction) -> None:
        """Handle `/shop_item categories`: every category, active or not."""
        await interaction.response.defer(ephemeral=True)
        cats = await self._shop.categories(include_inactive=True)
        embed = render_category_list(cats, self._embeds)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # -- items -------------------------------------------------------------

    @shop_item.command(name="add", description="➕ Добавить товар")
    @app_commands.describe(
        категория="Ключ категории (см. /shop_item categories)",
        название="Название товара",
        описание="Описание — видно покупателю",
        цена="Цена в Coins",
        эффект="Что делает покупка",
        значение_эффекта="Процент/число/диапазон/JSON — зависит от эффекта",
        дней="Срок действия эффекта в днях — пусто = бессрочно",
        использований="Сколько сделок/раз работает — пусто = не считается использованиями",
        остаток="Сколько штук в наличии — пусто = без ограничения",
        лимит_на_игрока="Сколько раз один игрок может купить — пусто = без лимита",
        эмодзи="Эмодзи перед названием — необязательно",
        порядок="Меньше — выше в списке",
    )
    @app_commands.choices(эффект=_EFFECT_CHOICES)
    @admin_only()
    async def add(
        self,
        interaction: discord.Interaction,
        категория: str,
        название: str,
        описание: str,
        цена: app_commands.Range[int, 1],
        эффект: app_commands.Choice[str],
        значение_эффекта: str | None = None,
        дней: int | None = None,
        использований: int | None = None,
        остаток: int | None = None,
        лимит_на_игрока: int | None = None,
        эмодзи: str | None = None,
        порядок: int = 0,
    ) -> None:
        """Handle `/shop_item add`: add a new item to the assortment."""
        await interaction.response.defer(ephemeral=True)
        item = await self._shop.add_item(
            категория,
            название,
            описание,
            цена,
            эффект.value,
            effect_value=значение_эффекта,
            duration_days=дней,
            uses=использований,
            stock=остаток,
            per_player_limit=лимит_на_игрока,
            emoji=эмодзи,
            sort_order=порядок,
        )
        embed = self._embeds.success(
            "✅ Товар добавлен",
            f"**{item.name}** — {item.price_coins} Coins\n"
            f"🗂️ Категория: {item.category_key}\n"
            f"🎯 {describe_effect(item.effect_kind, item.effect_value)}",
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @shop_item.command(name="edit", description="✏️ Изменить товар")
    @app_commands.describe(
        товар="ID товара (см. /shop_item list)",
        категория="Новая категория — необязательно",
        название="Новое название — необязательно",
        описание="Новое описание — необязательно",
        цена="Новая цена в Coins — необязательно",
        эффект="Новый тип эффекта — необязательно",
        значение_эффекта="Новое значение эффекта — необязательно",
        дней="Новый срок действия в днях — необязательно",
        использований="Новое число использований — необязательно",
        остаток="Новый остаток — необязательно",
        лимит_на_игрока="Новый лимит на игрока — необязательно",
        эмодзи="Новый эмодзи — необязательно",
        порядок="Новый порядок сортировки — необязательно",
        активен="Показывать ли на витрине — необязательно",
        без_срока="Снять срок действия (сделать бессрочным)",
        без_лимита_использований="Снять лимит по числу использований",
        без_ограничения_остатка="Снять ограничение остатка",
        без_лимита_на_игрока="Снять лимит на игрока",
    )
    @app_commands.choices(эффект=_EFFECT_CHOICES)
    @admin_only()
    async def edit(
        self,
        interaction: discord.Interaction,
        товар: int,
        категория: str | None = None,
        название: str | None = None,
        описание: str | None = None,
        цена: int | None = None,
        эффект: app_commands.Choice[str] | None = None,
        значение_эффекта: str | None = None,
        дней: int | None = None,
        использований: int | None = None,
        остаток: int | None = None,
        лимит_на_игрока: int | None = None,
        эмодзи: str | None = None,
        порядок: int | None = None,
        активен: bool | None = None,
        без_срока: bool = False,
        без_лимита_использований: bool = False,
        без_ограничения_остатка: bool = False,
        без_лимита_на_игрока: bool = False,
    ) -> None:
        """Handle `/shop_item edit`: change some fields, leaving the rest as-is."""
        await interaction.response.defer(ephemeral=True)
        current = await self._shop.get_item(товар)
        if current is None:
            await self._send_error(interaction, f"Товар #{товар} не найден.")
            return

        effect_value = значение_эффекта if значение_эффекта is not None else current.effect_value
        duration_days = None if без_срока else (дней if дней is not None else current.duration_days)
        uses = (
            None
            if без_лимита_использований
            else (использований if использований is not None else current.uses)
        )
        stock = (
            None if без_ограничения_остатка else (остаток if остаток is not None else current.stock)
        )
        per_player_limit = (
            None
            if без_лимита_на_игрока
            else (лимит_на_игрока if лимит_на_игрока is not None else current.per_player_limit)
        )

        item = await self._shop.update_item(
            товар,
            category_key=категория,
            name=название,
            description=описание,
            price_coins=цена,
            effect_kind=эффект.value if эффект is not None else None,
            effect_value=effect_value,
            duration_days=duration_days,
            uses=uses,
            stock=stock,
            per_player_limit=per_player_limit,
            emoji=эмодзи if эмодзи is not None else current.emoji,
            sort_order=порядок,
            active=активен,
        )
        await interaction.followup.send(embed=render_admin_item(item, self._embeds), ephemeral=True)

    @shop_item.command(name="delete", description="🗑️ Убрать товар с витрины (мягкое удаление)")
    @app_commands.describe(товар="ID товара (см. /shop_item list)")
    @admin_only()
    async def delete(self, interaction: discord.Interaction, товар: int) -> None:
        """Handle `/shop_item delete`: confirm, then soft-delete an item."""
        item = await self._shop.get_item(товар)
        if item is None:
            await interaction.response.defer(ephemeral=True)
            await self._send_error(interaction, f"Товар #{товар} не найден.")
            return

        embed = self._embeds.warning(
            "⚠️ Убрать товар с витрины?",
            f"«{item.name}» больше нельзя будет купить. Уже купленное и история покупок "
            "останутся как есть.",
        )
        view = ConfirmView(author_id=interaction.user.id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        view.message = await interaction.original_response()
        await view.wait()
        if not view.confirmed:
            await interaction.followup.send(
                embed=self._embeds.info("Отменено", "Товар остался на витрине."), ephemeral=True
            )
            return

        removed = await self._shop.delete_item(товар)
        await interaction.followup.send(
            embed=self._embeds.success("🗑️ Товар убран с витрины", f"«{removed.name}»."),
            ephemeral=True,
        )

    @shop_item.command(name="list", description="📋 Список товаров, можно по категории")
    @app_commands.describe(категория="Ключ категории — необязательно")
    @admin_only()
    async def list_items(
        self, interaction: discord.Interaction, категория: str | None = None
    ) -> None:
        """Handle `/shop_item list`: every item, active or not, paginated."""
        await interaction.response.defer(ephemeral=True)
        items = await self._shop.items(category_key=категория, include_hidden=True)
        chunks = _chunk(items, _LIST_PAGE_SIZE) or [()]
        title = "🛍️ Товары" if категория is None else f"🛍️ Товары — {категория}"
        pages = [
            render_admin_item_list(chunk, self._embeds, title=title, page=index, pages=len(chunks))
            for index, chunk in enumerate(chunks, start=1)
        ]
        for page in pages:
            await interaction.followup.send(embed=page, ephemeral=True)

    # -- refunds -------------------------------------------------------------

    @shop_item.command(name="recent", description="🧾 Последние покупки (для возврата)")
    @app_commands.describe(лимит="Сколько показать, по умолчанию 25")
    @admin_only()
    async def recent(
        self, interaction: discord.Interaction, лимит: app_commands.Range[int, 1, 100] = 25
    ) -> None:
        """Handle `/shop_item recent`: the admin's own picker for `/shop_item refund`."""
        await interaction.response.defer(ephemeral=True)
        purchases = await self._shop.recent_purchases(limit=лимит)
        rows = await self._resolve_recent(purchases)
        embed = render_recent_purchases(rows, self._embeds)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @shop_item.command(name="refund", description="↩️ Вернуть покупку")
    @app_commands.describe(покупка="ID покупки (см. /shop_item recent)")
    @admin_only()
    async def refund(self, interaction: discord.Interaction, покупка: int) -> None:
        """Handle `/shop_item refund`: credit back the price, undo its effects."""
        await interaction.response.defer(ephemeral=True)
        result = await self._shop.refund(покупка, admin_id=interaction.user.id)
        buyer = await self._players.get_by_id(result.purchase.player_id)
        embed = self._embeds.success(
            "↩️ Покупка возвращена",
            f"«{result.item.name}»"
            + (f" — {buyer.nick_display}" if buyer is not None else "")
            + f"\n💰 Возвращено: {result.refunded_coins} Coins.\n"
            f"💳 Новый баланс: {result.new_balance} Coins.\n"
            f"🎯 Эффектов отменено: {len(result.reversed_effects)}.",
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def _resolve_recent(
        self, purchases: Sequence[ShopPurchase]
    ) -> list[tuple[ShopPurchase, ShopItem | None, Player | None]]:
        item_cache: dict[int, ShopItem | None] = {}
        player_cache: dict[int, Player | None] = {}
        rows: list[tuple[ShopPurchase, ShopItem | None, Player | None]] = []
        for purchase in purchases:
            if purchase.shop_item_id not in item_cache:
                item_cache[purchase.shop_item_id] = await self._shop.get_item(purchase.shop_item_id)
            if purchase.player_id not in player_cache:
                player_cache[purchase.player_id] = await self._players.get_by_id(purchase.player_id)
            rows.append(
                (purchase, item_cache[purchase.shop_item_id], player_cache[purchase.player_id])
            )
        return rows

    # -- autocomplete --------------------------------------------------------

    @category.autocomplete("ключ")
    @add.autocomplete("категория")
    @list_items.autocomplete("категория")
    async def _category_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        query = current.casefold()
        categories = await self._shop.categories(include_inactive=True)
        return [
            app_commands.Choice(name=f"{c.name} ({c.key})"[:100], value=c.key)
            for c in categories
            if query in c.key.casefold() or query in c.name.casefold()
        ][:_MAX_CHOICES]

    @edit.autocomplete("товар")
    @delete.autocomplete("товар")
    async def _item_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        query = current.casefold()
        items = await self._shop.items(include_hidden=True)
        matches = [item for item in items if query in item.name.casefold() or query == str(item.id)]
        return [
            app_commands.Choice(name=f"#{item.id} {item.name}"[:100], value=item.id or 0)
            for item in matches[:_MAX_CHOICES]
        ]

    async def _send_error(self, interaction: discord.Interaction, message: str) -> None:
        embed = self._embeds.error("Ошибка", message)
        await interaction.followup.send(embed=embed, ephemeral=True)


def _chunk[T](items: Sequence[T], size: int) -> list[Sequence[T]]:
    return [items[i : i + size] for i in range(0, len(items), size)]
