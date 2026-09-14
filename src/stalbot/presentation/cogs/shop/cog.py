"""`/shop`, `/shop_purchases` — the Coins shop storefront (заявка 13.09.2026 п.2).

Player-facing only: browsing, buying, and looking back at what was bought.
The admin side (editing the assortment, refunds) is `ShopAdminCog`.
`PlayerNotLinkedError` is never caught here — it is curated user-facing
text (§`presentation/errors.py`'s `_DOMAIN_MESSAGES` fallback), so it is
left to propagate to the global slash-command error handler like every
other domain error a plain `/command` raises.
"""

from collections.abc import Sequence

import discord
from discord import app_commands
from discord.ext import commands

from stalbot.application.services.shop import ShopService
from stalbot.domain.entities.shop import ShopItem, ShopPurchase
from stalbot.presentation.cogs.shop.card import render_live_effects, render_purchase_history
from stalbot.presentation.cogs.shop.views import ShopBrowseView
from stalbot.presentation.embeds.factory import EmbedFactory
from stalbot.presentation.views.paginated_embed import PaginatedEmbedView

_PURCHASE_PAGE_SIZE = 10


class ShopCog(commands.Cog):
    """`/shop` and `/shop_purchases` — everything a player does with Coins."""

    def __init__(self, shop: ShopService, embeds: EmbedFactory) -> None:
        """Wire the cog to the service it delegates to.

        Args:
            shop: All browsing, balance, and purchase logic.
            embeds: Builds every embed this cog sends.
        """
        self._shop = shop
        self._embeds = embeds

    @app_commands.command(name="shop", description="🛒 Магазин — потратьте Coins")
    async def shop(self, interaction: discord.Interaction) -> None:
        """Handle `/shop`: open the category → item → buy flow."""
        await interaction.response.defer(ephemeral=True)
        balance = await self._shop.balance(interaction.user.id)
        categories = await self._shop.categories()
        view = ShopBrowseView(
            self._shop,
            categories,
            author_id=interaction.user.id,
            discord_id=interaction.user.id,
            embeds=self._embeds,
            balance=balance,
        )
        message = await interaction.followup.send(
            embed=view.embed, view=view, ephemeral=True, wait=True
        )
        view.message = message

    @app_commands.command(
        name="shop_purchases", description="📜 Мои покупки и активные эффекты магазина"
    )
    async def shop_purchases(self, interaction: discord.Interaction) -> None:
        """Handle `/shop_purchases`: purchase history, paginated, plus live effects."""
        await interaction.response.defer(ephemeral=True)
        purchases = await self._shop.my_purchases(interaction.user.id)
        effects = await self._shop.my_live_effects(interaction.user.id)

        rows = await self._resolve_items(purchases)
        chunks = _chunk(rows, _PURCHASE_PAGE_SIZE) or [()]
        pages = [
            render_purchase_history(chunk, self._embeds, page=index, pages=len(chunks))
            for index, chunk in enumerate(chunks, start=1)
        ]
        if len(pages) == 1:
            await interaction.followup.send(embed=pages[0], ephemeral=True)
        else:
            pager = PaginatedEmbedView(pages=pages, author_id=interaction.user.id)
            message = await interaction.followup.send(
                embed=pager.current, view=pager, ephemeral=True, wait=True
            )
            pager.message = message
        effects_embed = render_live_effects(effects, self._embeds)
        await interaction.followup.send(embed=effects_embed, ephemeral=True)

    async def _resolve_items(
        self, purchases: Sequence[ShopPurchase]
    ) -> list[tuple[ShopPurchase, ShopItem | None]]:
        cache: dict[int, ShopItem | None] = {}
        rows: list[tuple[ShopPurchase, ShopItem | None]] = []
        for purchase in purchases:
            if purchase.shop_item_id not in cache:
                cache[purchase.shop_item_id] = await self._shop.get_item(purchase.shop_item_id)
            rows.append((purchase, cache[purchase.shop_item_id]))
        return rows


def _chunk[T](items: Sequence[T], size: int) -> list[Sequence[T]]:
    return [items[i : i + size] for i in range(0, len(items), size)]
