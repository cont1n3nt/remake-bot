"""`/cost`, `/precost` — shelter crafting cost lookup and price-change preview.

sqlite_migration.md §V.2. Both commands are read-only: `/cost` shows the
currently resolved cost-of-goods (`ShelterCostService.current_costs`),
`/precost` previews what would change if some items' prices were different,
without writing anything (`ShelterCostService.precost`).

заявка 13.09.2026 п.10 and п.12 widened both: `/cost` takes an optional
`категория` that narrows the list to the shelter items behind the catalog's
ресурсы or бусты (via `catalog_items.shelter_item_id`), and `/precost`
takes up to four предмет/цена pairs instead of one, so a whole price move
can be previewed at once.
"""

from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_UP
from typing import Final

import discord
from discord import app_commands
from discord.ext import commands

from stalbot.application.dto.precost_diff import PrecostDiff
from stalbot.application.services.shelter_cost import ShelterCostService
from stalbot.domain.enums import ItemCategory
from stalbot.domain.money import evaluate_amount, format_kopeks
from stalbot.domain.shelter.cost import CostResult
from stalbot.infrastructure.cache.repositories.catalog_items import CatalogItemsRepository
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

_CATEGORY_LABEL: Final[dict[ItemCategory, str]] = {
    ItemCategory.RESOURCE: "📦 Предметы (скупка)",
    ItemCategory.BOOST: "🚀 Бусты (продажа)",
}

_UNLINKED_NOTE: Final = (
    "Показаны только предметы убежки, связанные с каталогом. "
    "Если чего-то не хватает — предмет ещё не привязан к убежке."
)


class ShelterCostCog(commands.Cog):
    """`/cost` and `/precost`."""

    def __init__(
        self,
        shelter_cost: ShelterCostService,
        shelter: ShelterRepository,
        catalog_items: CatalogItemsRepository,
        embeds: EmbedFactory,
    ) -> None:
        """Wire the cog to the service it delegates to.

        Args:
            shelter_cost: Computes cost-of-goods, live or hypothetical.
            shelter: Read-only lookup, for autocomplete over shelter items.
            catalog_items: Read-only lookup, for `/cost`'s категория filter —
                the catalog is what knows which side of the trade an item is
                on; `shelter_items` only knows how it is crafted.
            embeds: Builds every embed this cog sends.
        """
        self._shelter_cost = shelter_cost
        self._shelter = shelter
        self._catalog_items = catalog_items
        self._embeds = embeds

    @app_commands.command(name="cost", description="🛡️ [Админ] 📐 Себестоимость предмета убежки")
    @app_commands.describe(
        предмет="Предмет убежки (не указан — список по всем предметам)",
        категория="Показать только предметы скупки или только бусты — необязательно",
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
    async def cost(
        self,
        interaction: discord.Interaction,
        предмет: int | None = None,
        категория: app_commands.Choice[str] | None = None,
    ) -> None:
        """Handle `/cost`: one item's cost, or a paginated list, optionally by category."""
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
                f"📐 Себестоимость — {names.get(предмет, str(предмет))}", _format_result(result)
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        title = "📐 Себестоимость"
        note: str | None = None
        if категория is not None:
            category = ItemCategory(категория.value)
            linked = await self._linked_shelter_ids(category)
            costs = {item_id: result for item_id, result in costs.items() if item_id in linked}
            title = f"📐 Себестоимость — {_CATEGORY_LABEL[category]}"
            note = _UNLINKED_NOTE

        pages = _build_cost_pages(costs, names, self._embeds, title=title, note=note)
        if len(pages) == 1:
            await interaction.followup.send(embed=pages[0], ephemeral=True)
            return
        pager = PaginatedEmbedView(pages=pages, author_id=interaction.user.id)
        message = await interaction.followup.send(
            embed=pager.current, view=pager, ephemeral=True, wait=True
        )
        pager.message = message

    async def _linked_shelter_ids(self, category: ItemCategory) -> frozenset[int]:
        """`shelter_items.id`s reachable from the catalog's *category* (заявка п.10)."""
        return frozenset(
            item.shelter_item_id
            for item in await self._catalog_items.by_category(category)
            if item.shelter_item_id is not None
        )

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
        предмет_2="Ещё один предмет — необязательно",
        новая_цена_2="Цена для второго предмета",
        предмет_3="Ещё один предмет — необязательно",
        новая_цена_3="Цена для третьего предмета",
        предмет_4="Ещё один предмет — необязательно",
        новая_цена_4="Цена для четвёртого предмета",
    )
    @admin_only()
    async def precost(
        self,
        interaction: discord.Interaction,
        предмет: int,
        новая_цена: str,
        предмет_2: int | None = None,
        новая_цена_2: str | None = None,
        предмет_3: int | None = None,
        новая_цена_3: str | None = None,
        предмет_4: int | None = None,
        новая_цена_4: str | None = None,
    ) -> None:
        """Handle `/precost`: preview cost changes without applying any new price."""
        await interaction.response.defer(ephemeral=True)

        pairs = [
            (предмет, новая_цена),
            (предмет_2, новая_цена_2),
            (предмет_3, новая_цена_3),
            (предмет_4, новая_цена_4),
        ]
        incomplete = [
            position
            for position, (item_id, price) in enumerate(pairs, start=1)
            if (item_id is None) != (price is None)
        ]
        if incomplete:
            positions = ", ".join(str(position) for position in incomplete)
            embed = self._embeds.error(
                "Ошибка",
                "Для каждого предмета нужна цена, и наоборот. "
                f"Не хватает половины пары в позиции: {positions}.",
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        overrides: dict[int, int] = {}
        for item_id, price in pairs:
            if item_id is None or price is None:
                continue
            amount = evaluate_amount(price)
            overrides[item_id] = int((amount * 100).to_integral_value(rounding=ROUND_HALF_UP))

        diffs = await self._shelter_cost.precost(overrides)

        names = {
            item.id: item.name for item in await self._shelter.all_items() if item.id is not None
        }
        header = _format_overrides(overrides, names)
        if not diffs:
            embed = self._embeds.info(
                "ℹ️ Изменений нет",
                f"{header}\n\nПри таких ценах себестоимость других предметов не меняется.",
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        embed = self._embeds.info("🔍 Предпросмотр", f"{header}\n\n{_format_diffs(diffs)}")
        await interaction.followup.send(embed=enforce_limits(embed), ephemeral=True)

    @precost.autocomplete("предмет")
    @precost.autocomplete("предмет_2")
    @precost.autocomplete("предмет_3")
    @precost.autocomplete("предмет_4")
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


def _format_overrides(overrides: Mapping[int, int], names: Mapping[int, str]) -> str:
    """The "what if" the diff below is answering — every override, spelled out."""
    return "\n".join(
        f"🧪 {names.get(item_id, str(item_id))} → {format_kopeks(kopeks)}"
        for item_id, kopeks in overrides.items()
    )


def _format_diffs(diffs: Sequence[PrecostDiff]) -> str:
    lines = []
    for diff in diffs:
        before = _cost_text(diff.before_kopeks)
        after = _cost_text(diff.after_kopeks)
        lines.append(f"**{diff.item_name}**: {before} → {after}")
    return "\n".join(lines)


def _build_cost_pages(
    costs: dict[int, CostResult],
    names: dict[int, str],
    embeds: EmbedFactory,
    *,
    title: str = "📐 Себестоимость",
    note: str | None = None,
) -> list[discord.Embed]:
    entries = sorted(
        ((names.get(item_id, str(item_id)), result) for item_id, result in costs.items()),
        key=lambda entry: entry[0],
    )
    chunks = _chunk(entries, _COST_LIST_PAGE_SIZE) or [()]
    pages: list[discord.Embed] = []
    for index, chunk in enumerate(chunks, start=1):
        page_title = title if len(chunks) == 1 else f"{title} (стр. {index}/{len(chunks)})"
        if not chunk:
            pages.append(embeds.info(page_title, note or "Пока нет предметов."))
            continue
        embed = embeds.info(page_title, note if index == 1 else None)
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
