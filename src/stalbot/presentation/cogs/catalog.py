"""`/item_add`, `/del_item`, `/price_list`, `/give_price` — catalog CRUD & browsing.

PLAN.md §10.4-§10.5, §10.9.
"""

import io
from collections.abc import Sequence
from typing import Final

import discord
from discord import app_commands
from discord.ext import commands

from stalbot.application.services.catalog import CatalogService
from stalbot.application.services.pricing import PricingService
from stalbot.domain.entities.item import Item
from stalbot.domain.enums import ItemCategory
from stalbot.domain.money import evaluate_amount, format_amount
from stalbot.infrastructure.cache.repositories.items import ItemsCacheRepository
from stalbot.infrastructure.discord.emoji_resolver import EmojiResolver
from stalbot.presentation.autocomplete import item_choices
from stalbot.presentation.checks import admin_only
from stalbot.presentation.embeds.factory import EmbedFactory, enforce_limits
from stalbot.presentation.views.base import AuthorLockedView

_CATEGORY_LABEL: Final[dict[ItemCategory, str]] = {
    ItemCategory.RESOURCE: "📦 Ресурс",
    ItemCategory.BOOST: "🚀 Буст",
}
_PRICE_LIST_PAGE_SIZE: Final = 15


class CatalogCog(commands.Cog):
    """`/item_add`, `/del_item`, `/price_list`, `/give_price`."""

    def __init__(
        self,
        catalog: CatalogService,
        pricing: PricingService,
        items: ItemsCacheRepository,
        emojis: EmojiResolver,
        embeds: EmbedFactory,
    ) -> None:
        """Wire the cog to the services it delegates to.

        Args:
            catalog: Adds/removes catalog entries.
            pricing: Only used here for `export_txt` (`/give_price`).
            items: Read-only lookup for `/price_list` and autocomplete.
            emojis: Resolves an item's stored emoji name for display.
            embeds: Builds every embed this cog sends.
        """
        self._catalog = catalog
        self._pricing = pricing
        self._items = items
        self._emojis = emojis
        self._embeds = embeds

    @app_commands.command(name="item_add", description="🛡️ [Админ] ➕ Добавить предмет в базу")
    @app_commands.describe(
        название="Название предмета",
        категория="Ресурс или буст",
        цена_покупки="Цена скупки, например 250000 или 250к (опционально)",
        цена_продажи="Цена продажи, например 300000 или 300к (опционально)",
        эмодзи="Имя кастомного эмодзи на сервере (опционально)",
    )
    @app_commands.choices(
        категория=[
            app_commands.Choice(
                name=_CATEGORY_LABEL[ItemCategory.RESOURCE], value=ItemCategory.RESOURCE.value
            ),
            app_commands.Choice(
                name=_CATEGORY_LABEL[ItemCategory.BOOST], value=ItemCategory.BOOST.value
            ),
        ]
    )
    @admin_only()
    async def item_add(
        self,
        interaction: discord.Interaction,
        название: str,
        категория: app_commands.Choice[str],
        цена_покупки: str | None = None,
        цена_продажи: str | None = None,
        эмодзи: str | None = None,
    ) -> None:
        """Handle `/item_add`: create a new catalog entry."""
        await interaction.response.defer(ephemeral=True)
        category = ItemCategory(категория.value)
        price_buy = evaluate_amount(цена_покупки) if цена_покупки else None
        price_sell = evaluate_amount(цена_продажи) if цена_продажи else None

        item = await self._catalog.add_item(
            name=название,
            category=category,
            price_buy=price_buy,
            price_sell=price_sell,
            emoji=эмодзи,
        )

        lines = [*_item_summary_lines(item)]
        if эмодзи and not self._emojis.exists(эмодзи):
            lines.append("⚠️ Эмодзи с таким именем не найдено на сервере.")

        embed = self._embeds.success("✅ Предмет добавлен", "\n".join(lines))
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="del_item", description="🛡️ [Админ] ➖ Удалить предмет из базы")
    @app_commands.describe(предмет="Название предмета")
    @admin_only()
    async def del_item(self, interaction: discord.Interaction, предмет: int) -> None:
        """Handle `/del_item`: remove a catalog entry and renumber the block."""
        await interaction.response.defer(ephemeral=True)
        result = await self._catalog.delete_item(предмет)

        lines = [*_item_summary_lines(result.deleted)]
        if result.affected_order_channels:
            lines.append(
                f"⚠️ Затронуты черновики заказов бустов в "
                f"{len(result.affected_order_channels)} канале(ах)."
            )

        embed = self._embeds.success("🗑️ Предмет удалён", "\n".join(lines))
        await interaction.followup.send(embed=embed, ephemeral=True)

    @del_item.autocomplete("предмет")
    async def _del_item_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        """Fuzzy-match against the whole catalog; the choice's value is the item id."""
        return item_choices(await self._items.all(), current)

    @app_commands.command(name="price_list", description="🛡️ [Админ] 📋 Показать список цен")
    @admin_only()
    async def price_list(self, interaction: discord.Interaction) -> None:
        """Handle `/price_list`: browse the catalog by category, paginated."""
        await interaction.response.defer(ephemeral=True)
        pages = {
            ItemCategory.RESOURCE: self._build_price_pages(
                await self._items.by_category(ItemCategory.RESOURCE)
            ),
            ItemCategory.BOOST: self._build_price_pages(
                await self._items.by_category(ItemCategory.BOOST)
            ),
        }
        view = _PriceListView(pages=pages, author_id=interaction.user.id)
        message = await interaction.followup.send(
            embed=view.current, view=view, ephemeral=True, wait=True
        )
        view.message = message

    @app_commands.command(name="give_price", description="🛡️ [Админ] 📤 Выгрузить прайс-лист в TXT")
    @admin_only()
    async def give_price(self, interaction: discord.Interaction) -> None:
        """Handle `/give_price`: export the whole catalog as a TXT attachment."""
        await interaction.response.defer(ephemeral=True)
        text = await self._pricing.export_txt()
        file = discord.File(io.BytesIO(text.encode("utf-8-sig")), filename="price_list.txt")
        await interaction.followup.send(file=file, ephemeral=True)

    def _build_price_pages(self, items: Sequence[Item]) -> list[discord.Embed]:
        chunks = _chunk(items, _PRICE_LIST_PAGE_SIZE) or [()]
        pages: list[discord.Embed] = []
        for index, chunk in enumerate(chunks, start=1):
            title = "📋 Цены" if len(chunks) == 1 else f"📋 Цены (стр. {index}/{len(chunks)})"
            if not chunk:
                pages.append(self._embeds.info(title, "Пока нет предметов."))
                continue
            embed = self._embeds.info(title)
            for item in chunk:
                name, value = self._format_price_field(item)
                embed.add_field(name=name, value=value, inline=True)
            pages.append(enforce_limits(embed))
        return pages

    def _format_price_field(self, item: Item) -> tuple[str, str]:
        emoji = self._emojis.resolve(item.emoji) or "•"
        lines = []
        if item.price_buy is not None:
            lines.append(f"Скуп: {format_amount(item.price_buy)}")
        if item.price_sell is not None:
            lines.append(f"Продажа: {format_amount(item.price_sell)}")
        return f"{emoji} {item.name}", "\n".join(lines) or "—"


class _PriceListView(AuthorLockedView):
    """Category toggle + internal pagination for `/price_list` (PLAN.md §10.4)."""

    def __init__(
        self,
        *,
        pages: dict[ItemCategory, list[discord.Embed]],
        author_id: int,
        timeout: float = 180.0,
    ) -> None:
        """Build the view.

        Args:
            pages: Pre-built embeds, one list per category (already paged).
            author_id: The only Discord user id allowed to interact.
            timeout: Seconds before the view disables itself.
        """
        super().__init__(author_id=author_id, timeout=timeout)
        self._pages = pages
        self._category = ItemCategory.RESOURCE
        self._index = 0
        self._sync()

    @property
    def current(self) -> discord.Embed:
        """The embed for the current category/page."""
        return self._pages[self._category][self._index]

    @discord.ui.button(label="📦 Цены на ресурсы", style=discord.ButtonStyle.primary, row=0)
    async def show_resources(
        self, interaction: discord.Interaction, button: discord.ui.Button["_PriceListView"]
    ) -> None:
        """Switch to the resource category."""
        self._category = ItemCategory.RESOURCE
        self._index = 0
        await self._render(interaction)

    @discord.ui.button(label="🚀 Цены на бусты", style=discord.ButtonStyle.secondary, row=0)
    async def show_boosts(
        self, interaction: discord.Interaction, button: discord.ui.Button["_PriceListView"]
    ) -> None:
        """Switch to the boost category."""
        self._category = ItemCategory.BOOST
        self._index = 0
        await self._render(interaction)

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary, row=1)
    async def previous_page(
        self, interaction: discord.Interaction, button: discord.ui.Button["_PriceListView"]
    ) -> None:
        """Go back one page within the current category."""
        self._index = max(0, self._index - 1)
        await self._render(interaction)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary, row=1)
    async def next_page(
        self, interaction: discord.Interaction, button: discord.ui.Button["_PriceListView"]
    ) -> None:
        """Go forward one page within the current category."""
        self._index = min(len(self._pages[self._category]) - 1, self._index + 1)
        await self._render(interaction)

    async def _render(self, interaction: discord.Interaction) -> None:
        self._sync()
        await interaction.response.edit_message(embed=self.current, view=self)

    def _sync(self) -> None:
        resources_active = self._category is ItemCategory.RESOURCE
        self.show_resources.disabled = resources_active
        self.show_resources.style = (
            discord.ButtonStyle.primary if resources_active else discord.ButtonStyle.secondary
        )
        self.show_boosts.disabled = not resources_active
        self.show_boosts.style = (
            discord.ButtonStyle.secondary if resources_active else discord.ButtonStyle.primary
        )
        page_count = len(self._pages[self._category])
        self.previous_page.disabled = self._index == 0
        self.next_page.disabled = self._index >= page_count - 1


def _item_summary_lines(item: Item) -> list[str]:
    buy = format_amount(item.price_buy) if item.price_buy is not None else "—"
    sell = format_amount(item.price_sell) if item.price_sell is not None else "—"
    lines = [
        f"🆔 ID: {item.id}",
        f"📛 Название: {item.name}",
        f"🗂️ Категория: {_CATEGORY_LABEL[item.category]}",
        f"🪙 Цена покупки: {buy}",
        f"💰 Цена продажи: {sell}",
    ]
    if item.emoji:
        lines.append(f"🙂 Эмодзи: {item.emoji}")
    return lines


def _chunk[T](items: Sequence[T], size: int) -> list[Sequence[T]]:
    return [items[i : i + size] for i in range(0, len(items), size)]
