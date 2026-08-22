"""`/cost`, `/precost` — shelter crafting cost lookup and price-change preview.

sqlite_migration.md §V.2. Both commands are read-only: `/cost` shows the
currently resolved cost-of-goods (`ShelterCostService.current_costs`),
`/precost` previews what would change if one item's price were different,
without writing anything (`ShelterCostService.precost`).
"""

from collections.abc import Sequence
from decimal import ROUND_HALF_UP
from typing import Final

import discord
from discord import app_commands
from discord.ext import commands

from stalbot.application.dto.precost_diff import PrecostDiff
from stalbot.application.services.shelter_cost import ShelterCostService
from stalbot.domain.money import evaluate_amount, format_kopeks
from stalbot.domain.shelter.cost import CostResult
from stalbot.infrastructure.cache.repositories.shelter import ShelterRepository
from stalbot.presentation.autocomplete import shelter_item_choices
from stalbot.presentation.checks import admin_only
from stalbot.presentation.embeds.factory import EmbedFactory, enforce_limits
from stalbot.presentation.views.paginated_embed import PaginatedEmbedView

_COST_LIST_PAGE_SIZE: Final = 15

_SOURCE_LABEL: Final[dict[str, str]] = {
    "my_price": "своя цена",
    "market": "рыночная цена",
    "crafted": "крафт",
    "unresolved": "не определена",
}


class ShelterCostCog(commands.Cog):
    """`/cost` and `/precost`."""

    def __init__(
        self, shelter_cost: ShelterCostService, shelter: ShelterRepository, embeds: EmbedFactory
    ) -> None:
        """Wire the cog to the service it delegates to.

        Args:
            shelter_cost: Computes cost-of-goods, live or hypothetical.
            shelter: Read-only lookup, for autocomplete over shelter items.
            embeds: Builds every embed this cog sends.
        """
        self._shelter_cost = shelter_cost
        self._shelter = shelter
        self._embeds = embeds

    @app_commands.command(name="cost", description="🛡️ [Админ] 📐 Себестоимость предмета убежки")
    @app_commands.describe(предмет="Предмет убежки (не указан — список по всем предметам)")
    @admin_only()
    async def cost(self, interaction: discord.Interaction, предмет: int | None = None) -> None:
        """Handle `/cost`: one item's cost, or a paginated list of every item's cost."""
        await interaction.response.defer(ephemeral=True)
        costs = await self._shelter_cost.current_costs()
        items = await self._shelter.all_items()
        names = {item.id: item.name for item in items if item.id is not None}

        if предмет is not None:
            result = costs.get(предмет)
            if result is None:
                embed = self._embeds.error("Ошибка", "Предмет не найден в базе.")
                await interaction.followup.send(embed=embed, ephemeral=True)
                return
            embed = self._embeds.info(
                f"📐 Себестоимость — {names.get(предмет, f'#{предмет}')}",
                _format_result(result),
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        pages = _build_cost_pages(costs, names, self._embeds)
        if len(pages) == 1:
            await interaction.followup.send(embed=pages[0], ephemeral=True)
            return
        pager = PaginatedEmbedView(pages=pages, author_id=interaction.user.id)
        message = await interaction.followup.send(
            embed=pager.current, view=pager, ephemeral=True, wait=True
        )
        pager.message = message

    @cost.autocomplete("предмет")
    async def _cost_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return shelter_item_choices(await self._shelter.all_items(), current)

    @app_commands.command(
        name="precost", description="🛡️ [Админ] 🔍 Предпросмотр себестоимости при другой цене"
    )
    @app_commands.describe(
        предмет="Предмет убежки",
        новая_цена="Гипотетическая цена, например 3000 или 3к — ничего не сохраняется",
    )
    @admin_only()
    async def precost(
        self, interaction: discord.Interaction, предмет: int, новая_цена: str
    ) -> None:
        """Handle `/precost`: preview cost changes without applying the new price."""
        await interaction.response.defer(ephemeral=True)
        amount = evaluate_amount(новая_цена)
        new_kopeks = int((amount * 100).to_integral_value(rounding=ROUND_HALF_UP))

        diffs = await self._shelter_cost.precost(предмет, new_kopeks)
        if not diffs:
            embed = self._embeds.info(
                "ℹ️ Изменений нет", "При такой цене себестоимость других предметов не меняется."
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        embed = self._embeds.info(
            f"🔍 Предпросмотр — при цене {format_kopeks(new_kopeks)}", _format_diffs(diffs)
        )
        await interaction.followup.send(embed=enforce_limits(embed), ephemeral=True)

    @precost.autocomplete("предмет")
    async def _precost_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return shelter_item_choices(await self._shelter.all_items(), current)


def _format_result(result: CostResult) -> str:
    lines = [f"💰 Себестоимость: {_cost_text(result.cost_kopeks)}"]
    lines.append(f"📌 Источник: {_SOURCE_LABEL.get(result.source, result.source)}")
    if result.note:
        lines.append(f"📝 {result.note}")
    return "\n".join(lines)


def _format_diffs(diffs: Sequence[PrecostDiff]) -> str:
    lines = []
    for diff in diffs:
        before = _cost_text(diff.before_kopeks)
        after = _cost_text(diff.after_kopeks)
        lines.append(f"**{diff.item_name}**: {before} → {after}")
    return "\n".join(lines)


def _build_cost_pages(
    costs: dict[int, CostResult], names: dict[int, str], embeds: EmbedFactory
) -> list[discord.Embed]:
    entries = sorted(
        ((names.get(item_id, f"#{item_id}"), result) for item_id, result in costs.items()),
        key=lambda entry: entry[0],
    )
    chunks = _chunk(entries, _COST_LIST_PAGE_SIZE) or [()]
    pages: list[discord.Embed] = []
    for index, chunk in enumerate(chunks, start=1):
        title = (
            "📐 Себестоимость"
            if len(chunks) == 1
            else f"📐 Себестоимость (стр. {index}/{len(chunks)})"
        )
        if not chunk:
            pages.append(embeds.info(title, "Пока нет предметов."))
            continue
        embed = embeds.info(title)
        for name, result in chunk:
            source_label = _SOURCE_LABEL.get(result.source, result.source)
            embed.add_field(
                name=name,
                value=f"{_cost_text(result.cost_kopeks)} ({source_label})",
                inline=True,
            )
        pages.append(enforce_limits(embed))
    return pages


def _cost_text(cost_kopeks: int | None) -> str:
    return format_kopeks(cost_kopeks) if cost_kopeks is not None else "—"


def _chunk[T](items: Sequence[T], size: int) -> list[Sequence[T]]:
    return [items[i : i + size] for i in range(0, len(items), size)]
