"""The interactive category → item → confirm flow behind `/shop` (заявка 13.09.2026 п.2).

One throwaway view per invocation: browse, pick an item, confirm, buy. It
deliberately does not return to browsing after a successful purchase — the
view disables itself and shows a receipt instead. Buying something else
means running `/shop` again, which keeps this view from ever having to
reconcile a stale balance against a fresh one mid-session.
"""

from collections.abc import Sequence

import discord

from stalbot.application.services.shop import ShopService
from stalbot.domain.entities.shop import ShopCategory, ShopItem
from stalbot.presentation.cogs.shop.card import (
    render_categories,
    render_item_detail,
    render_items,
    render_purchase_receipt,
)
from stalbot.presentation.embeds.factory import EmbedFactory
from stalbot.presentation.views.error_view import ErrorReportingView

_MAX_OPTIONS = 25


class ShopBrowseView(ErrorReportingView):
    """`/shop`'s browsing view: a category select, then an item select, then buy."""

    def __init__(
        self,
        shop: ShopService,
        categories: Sequence[ShopCategory],
        *,
        author_id: int,
        discord_id: int,
        embeds: EmbedFactory,
        balance: int,
    ) -> None:
        """Build the view already showing the category picker.

        Args:
            shop: Looks up items and runs the purchase.
            categories: Every active category, in display order.
            author_id: The only Discord user allowed to interact.
            discord_id: The buyer's Discord account — may differ from
                `author_id` in theory, never in practice (`/shop` always
                buys as its own invoker).
            embeds: Builds every embed this view shows.
            balance: The buyer's Coins balance at the moment `/shop` ran.
        """
        super().__init__(author_id=author_id, embeds=embeds, timeout=180.0)
        self._shop = shop
        self._discord_id = discord_id
        self._categories = list(categories)
        self._items: Sequence[ShopItem] = ()
        self._category: ShopCategory | None = None
        self._item: ShopItem | None = None
        self._confirming = False
        self._balance = balance
        self._category_select: discord.ui.Select[ShopBrowseView] | None = None
        self._item_select: discord.ui.Select[ShopBrowseView] | None = None
        self._render_controls()

    @property
    def embed(self) -> discord.Embed:
        """The embed matching the view's current step."""
        if self._item is not None:
            return render_item_detail(
                self._item, self._embeds, balance=self._balance, confirming=self._confirming
            )
        if self._category is not None:
            return render_items(self._items, self._category, self._embeds, balance=self._balance)
        return render_categories(self._categories, self._embeds, balance=self._balance)

    def _render_controls(self) -> None:
        self.clear_items()

        category_select: discord.ui.Select[ShopBrowseView] = discord.ui.Select(
            placeholder="Выберите категорию",
            options=[
                discord.SelectOption(
                    label=category.name[:100],
                    value=category.key,
                    description=(category.description or "")[:100] or None,
                    default=self._category is not None and category.key == self._category.key,
                )
                for category in self._categories[:_MAX_OPTIONS]
            ]
            or [discord.SelectOption(label="Категорий нет", value="none")],
            row=0,
        )
        category_select.disabled = not self._categories or self._confirming
        category_select.callback = self._on_category_selected  # type: ignore[method-assign]
        self._category_select = category_select
        self.add_item(category_select)

        self._item_select = None
        if self._category is not None and not self._confirming:
            item_select: discord.ui.Select[ShopBrowseView] = discord.ui.Select(
                placeholder="Выберите товар",
                options=[
                    discord.SelectOption(
                        label=f"{item.name} — {item.price_coins} Coins"[:100],
                        value=str(item.id),
                        description=item.description[:100] or None,
                        default=self._item is not None and item.id == self._item.id,
                    )
                    for item in self._items[:_MAX_OPTIONS]
                ]
                or [discord.SelectOption(label="Товаров нет", value="none")],
                row=1,
            )
            item_select.disabled = not self._items
            item_select.callback = self._on_item_selected  # type: ignore[method-assign]
            self._item_select = item_select
            self.add_item(item_select)

        if self._item is not None:
            if self._confirming:
                confirm_button: discord.ui.Button[ShopBrowseView] = discord.ui.Button(
                    label="✅ Подтвердить", style=discord.ButtonStyle.success, row=2
                )
                confirm_button.callback = self._on_confirm  # type: ignore[method-assign]
                self.add_item(confirm_button)

                cancel_button: discord.ui.Button[ShopBrowseView] = discord.ui.Button(
                    label="❌ Отмена", style=discord.ButtonStyle.secondary, row=2
                )
                cancel_button.callback = self._on_cancel  # type: ignore[method-assign]
                self.add_item(cancel_button)
            else:
                buy_button: discord.ui.Button[ShopBrowseView] = discord.ui.Button(
                    label="🛒 Купить", style=discord.ButtonStyle.primary, row=2
                )
                buy_button.callback = self._on_buy_clicked  # type: ignore[method-assign]
                self.add_item(buy_button)

    async def _on_category_selected(self, interaction: discord.Interaction) -> None:
        assert self._category_select is not None  # noqa: S101 - set by _render_controls before this fires
        value = self._category_select.values[0] if self._category_select.values else None
        category = next((c for c in self._categories if c.key == value), None)
        if category is None:
            await interaction.response.defer()
            return
        self._category = category
        self._items = await self._shop.items(category_key=category.key)
        self._item = None
        self._confirming = False
        self._render_controls()
        await interaction.response.edit_message(embed=self.embed, view=self)

    async def _on_item_selected(self, interaction: discord.Interaction) -> None:
        assert self._item_select is not None  # noqa: S101 - set by _render_controls before this fires
        value = self._item_select.values[0] if self._item_select.values else None
        item = next((i for i in self._items if str(i.id) == value), None)
        if item is None:
            await interaction.response.defer()
            return
        self._item = item
        self._confirming = False
        self._render_controls()
        await interaction.response.edit_message(embed=self.embed, view=self)

    async def _on_buy_clicked(self, interaction: discord.Interaction) -> None:
        self._confirming = True
        self._render_controls()
        await interaction.response.edit_message(embed=self.embed, view=self)

    async def _on_cancel(self, interaction: discord.Interaction) -> None:
        self._confirming = False
        self._render_controls()
        await interaction.response.edit_message(embed=self.embed, view=self)

    async def _on_confirm(self, interaction: discord.Interaction) -> None:
        assert self._item is not None and self._item.id is not None  # noqa: S101 - only shown with an item selected
        assert interaction.message is not None  # noqa: S101 - a component interaction always has one
        balance_before = self._balance
        result = await self._shop.buy(
            self._discord_id,
            self._item.id,
            idempotency_key=f"shop_buy:{interaction.message.id}:{self._item.id}",
        )
        self._disable_children()
        embed = render_purchase_receipt(
            result.item,
            self._embeds,
            balance_before=balance_before,
            balance_after=result.new_balance,
        )
        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()
