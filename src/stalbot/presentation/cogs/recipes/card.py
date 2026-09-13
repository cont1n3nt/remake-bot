"""Rendering a recipe for Discord (заявка 13.09.2026 п.3).

The owner's question is always the same: «из чего это делается, сколько
энергии, сколько выходит и почём». So the card answers it in that order,
with energy pulled out of the ingredient list because it is the line they
asked for by name.
"""

from collections.abc import Sequence
from fractions import Fraction

import discord

from stalbot.application.services.recipes import IngredientView, RecipeView
from stalbot.domain.money import format_kopeks
from stalbot.domain.shelter.recipe_text import MAX_LEVEL, MIN_LEVEL, format_quantity
from stalbot.presentation.embeds.factory import EmbedFactory, enforce_limits

_SEPARATOR = "━━━━━━━━━━━━━━━━━━━━━"


def _cost_text(kopeks: int | None) -> str:
    return format_kopeks(kopeks) if kopeks is not None else "—"


def _ingredient_line(line: IngredientView) -> str:
    quantity = format_quantity(line.quantity)
    if line.unit_cost_kopeks is None:
        return f" • {line.name} × {quantity} — цена неизвестна"
    return (
        f" • {line.name} × {quantity} = {_cost_text(line.total_kopeks)}"
        f" ({_cost_text(line.unit_cost_kopeks)}/шт)"
    )


def _yields_line(view: RecipeView) -> str:
    """All five levels on one line, the current one marked.

    Showing every level rather than just the active one is the point: this
    is where «бонусные крафты» live, and the owner plans upgrades from it.
    """
    parts = []
    for level in range(MIN_LEVEL, MAX_LEVEL + 1):
        units = view.yields.get(level, Fraction(0))
        text = format_quantity(units) if units > 0 else "—"
        parts.append(f"**{level}:{text}**" if level == view.profession_level else f"{level}:{text}")
    return " · ".join(parts)


def render_recipe(view: RecipeView, embeds: EmbedFactory) -> discord.Embed:
    """Build the full card for one recipe.

    Args:
        view: The resolved recipe.
        embeds: Factory used to build the underlying `discord.Embed`.
    """
    lines: list[str] = [
        f"🏭 Профессия: {view.profession_name} (уровень {view.profession_level})",
        _SEPARATOR,
    ]

    materials = view.materials
    lines.append("📦 Ингредиенты:" if materials else "📦 Ингредиентов нет")
    lines.extend(_ingredient_line(line) for line in materials)

    energy = view.energy
    if energy is not None:
        lines.append("")
        lines.append(
            f"⚡ Энергия: {format_quantity(energy.quantity)} = {_cost_text(energy.total_kopeks)}"
        )

    lines.append(_SEPARATOR)
    lines.append(f"🎲 Выход за крафт по уровням: {_yields_line(view)}")

    if view.is_available_now:
        lines.append(
            f"📐 Себестоимость: {_cost_text(view.unit_cost_kopeks)}/шт "
            f"(крафт целиком: {_cost_text(view.batch_cost_kopeks)})"
        )
    else:
        lines.append(
            f"🔒 На уровне {view.profession_level} рецепт недоступен — "
            "себестоимость по нему не считается."
        )

    if view.source_sheet or view.source_cell:
        source = " / ".join(part for part in (view.source_sheet, view.source_cell) if part)
        lines.append(f"-# Импортировано из таблицы: {source}")

    embed = embeds.info(f"📜 {view.output_name} — рецепт #{view.recipe_id}", "\n".join(lines))
    return enforce_limits(embed)


def render_recipe_list(
    views: Sequence[RecipeView],
    embeds: EmbedFactory,
    *,
    title: str,
    page: int = 1,
    pages: int = 1,
) -> discord.Embed:
    """Build one page of the recipe list.

    Args:
        views: The recipes on this page.
        embeds: Factory used to build the underlying `discord.Embed`.
        title: Base title, paginated automatically.
        page: 1-based page number.
        pages: Total page count.
    """
    page_title = title if pages == 1 else f"{title} (стр. {page}/{pages})"
    if not views:
        return embeds.info(page_title, "Рецептов нет.")

    lines: list[str] = []
    for view in views:
        lock = "" if view.is_available_now else " 🔒"
        cost = _cost_text(view.unit_cost_kopeks) if view.is_available_now else "—"
        lines.append(
            f"**#{view.recipe_id}** {view.output_name}{lock} · {view.profession_name} · "
            f"{len(view.materials)} инг. · {cost}/шт"
        )
    return enforce_limits(embeds.info(page_title, "\n".join(lines)))
