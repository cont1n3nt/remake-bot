"""The one recipe-editing modal (заявка 13.09.2026 п.3).

Two text areas — ingredients and per-level yields — because that is the
whole of a recipe: what goes in, and how much comes out at each profession
level. Both are free text parsed by `domain.shelter.recipe_text`, so the
owner can paste a recipe in the same shape they read it off the sheet.
"""

from collections.abc import Awaitable, Callable

import discord

from stalbot.presentation.embeds.factory import EmbedFactory
from stalbot.presentation.views.error_modal import ErrorReportingModal

_RecipeSubmitHandler = Callable[[discord.Interaction, str, str], Awaitable[None]]

_INGREDIENTS_PLACEHOLDER = "Мякоть лимонника x2\nСпирт x1\nЭнергия x900"
_YIELDS_PLACEHOLDER = "1:4 2:4,5 3:5 4:6 5:6,5"
_TEXT_MAX = 2000


class RecipeFormModal(ErrorReportingModal):
    """Ingredients + per-level yields, for both `/recipe add` and `/recipe edit`."""

    def __init__(
        self,
        on_submit: _RecipeSubmitHandler,
        *,
        embeds: EmbedFactory,
        title: str = "📜 Рецепт",
        ingredients: str = "",
        yields: str = "",
    ) -> None:
        """Build the modal.

        Args:
            on_submit: Called with the interaction and the two raw texts.
            embeds: Factory used to build the error embed on a failure.
            title: Modal title — says whether this adds or edits.
            ingredients: Pre-filled ingredient text (`/recipe edit`).
            yields: Pre-filled yields text (`/recipe edit`).
        """
        super().__init__(title=title, embeds=embeds)
        self._on_submit_cb = on_submit
        self.ingredients: discord.ui.TextInput[RecipeFormModal] = discord.ui.TextInput(
            label="Ингредиенты (по одному в строке)",
            style=discord.TextStyle.paragraph,
            placeholder=_INGREDIENTS_PLACEHOLDER,
            default=ingredients or None,
            max_length=_TEXT_MAX,
        )
        self.yields: discord.ui.TextInput[RecipeFormModal] = discord.ui.TextInput(
            label="Выход за крафт по уровням 1-5",
            style=discord.TextStyle.short,
            placeholder=_YIELDS_PLACEHOLDER,
            default=yields or None,
            max_length=_TEXT_MAX,
        )
        self.add_item(self.ingredients)
        self.add_item(self.yields)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Forward both texts to the injected handler."""
        await self._on_submit_cb(interaction, str(self.ingredients.value), str(self.yields.value))
