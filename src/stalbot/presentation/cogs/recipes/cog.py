"""`/recipe` — просмотр и правка рецептов убежки (заявка 13.09.2026 п.3).

One command group instead of five commands, which is what the owner asked
for: «чтобы не делать много лишних команд». The data has been in the
database since the shelter import; this is the Discord layer that was never
built, so new recipes stop meaning a code change.

Ingredients and yields go through a modal rather than command options: a
recipe is a short list, not four scalars, and Discord's option limit would
cap it long before real recipes do.
"""

from collections.abc import Sequence
from decimal import ROUND_HALF_UP
from typing import Final

import discord
from discord import app_commands
from discord.ext import commands

from stalbot.application.services.recipes import RecipeService, RecipeView
from stalbot.domain.entities.shelter_item import ShelterItem
from stalbot.domain.money import evaluate_amount, format_kopeks
from stalbot.domain.shelter.recipe_text import (
    MAX_LEVEL,
    MIN_LEVEL,
    format_ingredients,
    format_yields,
    parse_ingredients,
    parse_yields,
)
from stalbot.infrastructure.cache.repositories.shelter import ShelterRepository
from stalbot.presentation.autocomplete import shelter_item_choices
from stalbot.presentation.checks import admin_only
from stalbot.presentation.cogs.recipes.card import render_recipe, render_recipe_list
from stalbot.presentation.cogs.recipes.modals import RecipeFormModal
from stalbot.presentation.embeds.factory import EmbedFactory
from stalbot.presentation.views.confirm import ConfirmView
from stalbot.presentation.views.paginated_embed import PaginatedEmbedView

_LIST_PAGE_SIZE: Final = 15
_MAX_CHOICES: Final = 25


class RecipesCog(commands.Cog):
    """The `/recipe` group: list, show, add, edit, delete, level."""

    recipe = app_commands.Group(name="recipe", description="🛡️ [Админ] 📜 Рецепты убежки")

    def __init__(
        self, recipes: RecipeService, shelter: ShelterRepository, embeds: EmbedFactory
    ) -> None:
        """Wire the cog to the service it delegates to.

        Args:
            recipes: All recipe reads and writes.
            shelter: Read-only lookup, for autocomplete over shelter items.
            embeds: Builds every embed this cog sends.
        """
        self._recipes = recipes
        self._shelter = shelter
        self._embeds = embeds
        #: Carries the target of an `add`/`edit` across the modal round trip —
        #: Discord gives a modal submission no memory of the command that
        #: opened it, and `custom_id` is the modal's, not per-invocation.
        self._pending: dict[int, tuple[str, int, str | None]] = {}

    # -- reads ---------------------------------------------------------------

    @recipe.command(name="list", description="📋 Все рецепты, можно по профессии")
    @app_commands.describe(профессия="Показать только рецепты одной профессии — необязательно")
    @admin_only()
    async def recipe_list(
        self, interaction: discord.Interaction, профессия: str | None = None
    ) -> None:
        """Handle `/recipe list`: browse every recipe, paginated."""
        await interaction.response.defer(ephemeral=True)
        views = await self._recipes.list_recipes(profession_key=профессия)
        title = "📜 Рецепты убежки"
        if профессия is not None:
            professions = await self._recipes.professions()
            title = f"📜 Рецепты — {professions[профессия][0]}"

        chunks = _chunk(views, _LIST_PAGE_SIZE) or [()]
        pages = [
            render_recipe_list(chunk, self._embeds, title=title, page=index, pages=len(chunks))
            for index, chunk in enumerate(chunks, start=1)
        ]
        await self._send_pages(interaction, pages)

    @recipe.command(name="show", description="🔍 Рецепты одного предмета")
    @app_commands.describe(предмет="Предмет убежки")
    @admin_only()
    async def recipe_show(self, interaction: discord.Interaction, предмет: int) -> None:
        """Handle `/recipe show`: every recipe producing one item."""
        await interaction.response.defer(ephemeral=True)
        views = await self._recipes.show(предмет)
        if not views:
            item = await self._shelter.get_item(предмет)
            name = item.name if item is not None else str(предмет)
            embed = self._embeds.info(
                "ℹ️ Рецептов нет",
                f"«{name}» ничем не крафтится — цена берётся из своей или рыночной.",
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        pages = [render_recipe(view, self._embeds) for view in views]
        await self._send_pages(interaction, pages)

    # -- writes --------------------------------------------------------------

    @recipe.command(name="add", description="➕ Добавить рецепт")
    @app_commands.describe(предмет="Что крафтится", профессия="Какая профессия крафтит")
    @admin_only()
    async def recipe_add(
        self, interaction: discord.Interaction, предмет: int, профессия: str
    ) -> None:
        """Handle `/recipe add`: open the form, pre-filled with nothing."""
        self._pending[interaction.user.id] = ("add", предмет, профессия)
        await interaction.response.send_modal(
            RecipeFormModal(self._on_recipe_submitted, embeds=self._embeds, title="📜 Новый рецепт")
        )

    @recipe.command(name="edit", description="✏️ Изменить рецепт")
    @app_commands.describe(
        рецепт="Рецепт (см. /recipe list или /recipe show)",
        профессия="Сменить профессию — необязательно",
    )
    @admin_only()
    async def recipe_edit(
        self, interaction: discord.Interaction, рецепт: int, профессия: str | None = None
    ) -> None:
        """Handle `/recipe edit`: open the form pre-filled with the current recipe."""
        view = await self._recipes.get(рецепт)
        self._pending[interaction.user.id] = ("edit", рецепт, профессия)
        await interaction.response.send_modal(
            RecipeFormModal(
                self._on_recipe_submitted,
                embeds=self._embeds,
                title=f"📜 Рецепт #{рецепт}",
                ingredients=format_ingredients(
                    tuple((line.name, line.quantity) for line in view.ingredients)
                ),
                yields=format_yields(view.yields),
            )
        )

    async def _on_recipe_submitted(
        self, interaction: discord.Interaction, ingredients_text: str, yields_text: str
    ) -> None:
        """Parse both texts, refuse anything unreadable, then write."""
        await interaction.response.defer(ephemeral=True)
        pending = self._pending.pop(interaction.user.id, None)
        if pending is None:
            embed = self._embeds.error(
                "Ошибка", "Форма устарела — откройте `/recipe add` или `/recipe edit` заново."
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        mode, target, profession_key = pending

        parsed_ingredients = parse_ingredients(ingredients_text)
        parsed_yields = parse_yields(yields_text)
        rejected = [*parsed_ingredients.rejected, *parsed_yields.rejected]
        if rejected:
            # Refused whole rather than applied in part: a recipe missing one
            # ingredient still produces a cost, just a quietly wrong one.
            listed = "\n".join(f" • `{line}`" for line in rejected[:10])
            embed = self._embeds.error(
                "Ошибка",
                "Не удалось разобрать строки — рецепт не сохранён:\n"
                f"{listed}\n\n"
                "Ингредиенты: `Название x2`. Выход: `1:4 2:4,5 3:5`.",
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        if mode == "add":
            assert profession_key is not None  # noqa: S101 - required option on /recipe add
            view = await self._recipes.create(
                target, profession_key, parsed_ingredients.ingredients, parsed_yields.yields
            )
            title = "✅ Рецепт добавлен"
        else:
            view = await self._recipes.update(
                target,
                profession_key=profession_key,
                ingredients=parsed_ingredients.ingredients,
                yields=parsed_yields.yields,
            )
            title = "✅ Рецепт обновлён"

        await interaction.followup.send(
            embed=self._embeds.success(title, "Себестоимость пересчитана по всей убежке."),
            ephemeral=True,
        )
        await interaction.followup.send(embed=render_recipe(view, self._embeds), ephemeral=True)

    @recipe.command(name="delete", description="🗑️ Удалить рецепт")
    @app_commands.describe(рецепт="Рецепт (см. /recipe list)")
    @admin_only()
    async def recipe_delete(self, interaction: discord.Interaction, рецепт: int) -> None:
        """Handle `/recipe delete`: confirm, then remove the recipe."""
        await interaction.response.defer(ephemeral=True)
        view = await self._recipes.get(рецепт)

        if not await self._confirm_delete(interaction, view):
            await interaction.followup.send(
                embed=self._embeds.info("Отменено", "Рецепт остался на месте."), ephemeral=True
            )
            return

        removed = await self._recipes.delete(рецепт)
        await interaction.followup.send(
            embed=self._embeds.success(
                "🗑️ Рецепт удалён",
                f"#{removed.recipe_id} — «{removed.output_name}» ({removed.profession_name}).\n"
                "Себестоимость пересчитана.",
            ),
            ephemeral=True,
        )

    async def _confirm_delete(self, interaction: discord.Interaction, view: RecipeView) -> bool:
        embed = self._embeds.warning(
            "⚠️ Подтвердите удаление",
            f"Удалить рецепт #{view.recipe_id} для «{view.output_name}» "
            f"({view.profession_name})?\n"
            "Себестоимость всего, что через него считалось, пересчитается.",
        )
        confirm = ConfirmView(author_id=interaction.user.id)
        message = await interaction.followup.send(
            embed=embed, view=confirm, ephemeral=True, wait=True
        )
        confirm.message = message
        await confirm.wait()
        return bool(confirm.confirmed)

    @recipe.command(name="level", description="🏭 Уровень профессии")
    @app_commands.describe(профессия="Какая профессия", уровень="Новый уровень, 1-5")
    @admin_only()
    async def recipe_level(
        self,
        interaction: discord.Interaction,
        профессия: str,
        уровень: app_commands.Range[int, MIN_LEVEL, MAX_LEVEL],
    ) -> None:
        """Handle `/recipe level`: change a profession's level and recompute."""
        await interaction.response.defer(ephemeral=True)
        name, level = await self._recipes.set_profession_level(профессия, уровень)
        embed = self._embeds.success(
            "🏭 Уровень изменён",
            f"{name}: уровень {level}.\n"
            "Это меняет выход за крафт, поэтому себестоимость пересчитана по всей убежке.",
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # -- предметы убежки, из которых состоят рецепты -------------------------

    @recipe.command(name="item_add", description="🧱 Добавить предмет убежки")
    @app_commands.describe(
        название="Название предмета, как вы его пишете",
        вид="Компонент покупается, крафт делается, энергия — расходник",
        своя_цена="Ваша цена, например 3000 или 3к — перебивает крафт и рынок",
        рыночная_цена="Рыночная цена, используется когда рецепта нет",
    )
    @app_commands.choices(
        вид=[
            app_commands.Choice(name="📦 Компонент", value="component"),
            app_commands.Choice(name="🔨 Крафт", value="craftable"),
            app_commands.Choice(name="⚡ Энергия/виртуальный", value="virtual"),
        ]
    )
    @admin_only()
    async def recipe_item_add(
        self,
        interaction: discord.Interaction,
        название: str,
        вид: str,
        своя_цена: str | None = None,
        рыночная_цена: str | None = None,
    ) -> None:
        """Handle `/recipe item_add`: create the shelter item a recipe will refer to."""
        await interaction.response.defer(ephemeral=True)
        item = await self._recipes.add_item(
            название,
            вид,
            my_kopeks=_to_kopeks(своя_цена),
            market_kopeks=_to_kopeks(рыночная_цена),
        )
        embed = self._embeds.success(
            "🧱 Предмет добавлен",
            f"📦 {item.name}\n"
            f"🗂️ Вид: {_KIND_LABEL.get(item.kind, item.kind)}\n"
            f"{_price_lines(item)}\n"
            "Теперь его можно указывать ингредиентом в `/recipe add`.",
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @recipe.command(name="item_price", description="💰 Цены предмета убежки")
    @app_commands.describe(
        предмет="Предмет убежки",
        своя_цена="Ваша цена — перебивает крафт и рынок",
        рыночная_цена="Рыночная цена",
        убрать_свою_цену="Снять свою цену, чтобы считалось по крафту или рынку",
    )
    @admin_only()
    async def recipe_item_price(
        self,
        interaction: discord.Interaction,
        предмет: int,
        своя_цена: str | None = None,
        рыночная_цена: str | None = None,
        убрать_свою_цену: bool = False,
    ) -> None:
        """Handle `/recipe item_price`: change prices and recompute everything they feed."""
        await interaction.response.defer(ephemeral=True)
        item = await self._recipes.set_item_prices(
            предмет,
            my_kopeks=_to_kopeks(своя_цена),
            market_kopeks=_to_kopeks(рыночная_цена),
            clear_my_price=убрать_свою_цену,
        )
        embed = self._embeds.success(
            "💰 Цены обновлены",
            f"📦 {item.name}\n{_price_lines(item)}\n"
            "Себестоимость всего, что через него считается, пересчитана.",
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @recipe_item_price.autocomplete("предмет")
    async def _price_item_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return shelter_item_choices(await self._shelter.all_items(), current)

    # -- autocomplete --------------------------------------------------------

    @recipe_show.autocomplete("предмет")
    @recipe_add.autocomplete("предмет")
    async def _item_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return shelter_item_choices(await self._shelter.all_items(), current)

    @recipe_list.autocomplete("профессия")
    @recipe_add.autocomplete("профессия")
    @recipe_edit.autocomplete("профессия")
    @recipe_level.autocomplete("профессия")
    async def _profession_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Shown by display name, submitted as the stable key."""
        query = current.casefold()
        professions = await self._recipes.professions()
        return [
            app_commands.Choice(name=f"{name} (ур. {level})", value=key)
            for key, (name, level) in sorted(professions.items(), key=lambda kv: kv[1][0])
            if query in name.casefold() or query in key
        ][:_MAX_CHOICES]

    @recipe_edit.autocomplete("рецепт")
    @recipe_delete.autocomplete("рецепт")
    async def _recipe_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        """Matched by produced item name, shown with the profession to disambiguate.

        27 items have more than one recipe (§II.2), so the id alone would
        not tell two of them apart in the picker.
        """
        query = current.casefold()
        views = await self._recipes.list_recipes()
        matches = [
            view
            for view in views
            if query in view.output_name.casefold() or query == str(view.recipe_id)
        ]
        return [
            app_commands.Choice(
                name=f"#{view.recipe_id} {view.output_name} · {view.profession_name}"[:100],
                value=view.recipe_id,
            )
            for view in matches[:_MAX_CHOICES]
        ]

    async def _send_pages(
        self, interaction: discord.Interaction, pages: list[discord.Embed]
    ) -> None:
        if len(pages) == 1:
            await interaction.followup.send(embed=pages[0], ephemeral=True)
            return
        pager = PaginatedEmbedView(pages=pages, author_id=interaction.user.id)
        message = await interaction.followup.send(
            embed=pager.current, view=pager, ephemeral=True, wait=True
        )
        pager.message = message


_KIND_LABEL: Final[dict[str, str]] = {
    "component": "📦 Компонент",
    "craftable": "🔨 Крафт",
    "virtual": "⚡ Энергия/виртуальный",
}


def _to_kopeks(raw: str | None) -> int | None:
    """Parse a typed ruble amount into whole kopecks, or `None` if not given."""
    if raw is None:
        return None
    amount = evaluate_amount(raw)
    return int((amount * 100).to_integral_value(rounding=ROUND_HALF_UP))


def _price_lines(item: ShelterItem) -> str:
    """The two prices that decide an item's cost, both always shown.

    A blank «своя цена» is information, not an omission: it is what makes
    the calculator fall through to the recipe.
    """
    my_price = format_kopeks(item.my_kopeks) if item.my_kopeks is not None else "не задана"
    market = format_kopeks(item.market_kopeks) if item.market_kopeks is not None else "не задана"
    return f"💰 Своя цена: {my_price}\n🏪 Рыночная: {market}"


def _chunk[T](items: Sequence[T], size: int) -> list[Sequence[T]]:
    return [items[i : i + size] for i in range(0, len(items), size)]
