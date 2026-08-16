"""`/poster` — post one of the three regenerated catalog posters (Часть IX, Э11).

Known simplification (first cut, same trade-off `/skupka` shipped with,
sqlite_migration.md Э8): posts a fresh message every call rather than
editing one tracked message, and there is no auto-regeneration hook on
`/setprice`/`/setboost`/`/give_price` yet — both are documented follow-ups,
not blockers, since `PosterService`/`PillowRenderer` underneath already
support being called as often as needed.
"""

import io

import discord
from discord import app_commands
from discord.ext import commands

from stalbot.application.ports.poster_renderer import PosterRenderer
from stalbot.application.services.posters import PosterService
from stalbot.domain.enums import PosterKind
from stalbot.presentation.checks import admin_only


class PostersCog(commands.Cog):
    """`/poster <тип>` — builds and posts the current catalog poster."""

    def __init__(self, posters: PosterService, renderer: PosterRenderer) -> None:
        """Wire the cog to the service/renderer it delegates to.

        Args:
            posters: Resolves a poster kind + live catalog prices into a spec.
            renderer: Draws the spec into PNG bytes.
        """
        self._posters = posters
        self._renderer = renderer

    @app_commands.command(name="poster", description="🛡️ [Админ] 🖼️ Плакат каталога")
    @app_commands.describe(тип="Какой плакат сгенерировать")
    @app_commands.choices(
        тип=[
            app_commands.Choice(name="Скупка ресурсов", value=PosterKind.RESOURCES.value),
            app_commands.Choice(name="Продажа бустов", value=PosterKind.BOOSTS.value),
            app_commands.Choice(name="Скупка бустов", value=PosterKind.BOOST_PURCHASES.value),
        ]
    )
    @admin_only()
    async def poster(self, interaction: discord.Interaction, тип: str) -> None:
        """Handle `/poster`: render the chosen kind and post it as an image."""
        await interaction.response.defer()
        kind = PosterKind(тип)
        spec = await self._posters.build(kind)
        png = self._renderer.render(spec)
        file = discord.File(io.BytesIO(png), filename=f"{kind.value}.png")
        await interaction.followup.send(file=file)
