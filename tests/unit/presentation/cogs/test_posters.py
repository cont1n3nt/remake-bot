"""Tests for `stalbot.presentation.cogs.posters.PostersCog` (Часть IX, Э11).

`PosterService`/`PosterRenderer` are mocked — their own behavior is covered
in `tests/unit/application/services/test_posters.py` and
`tests/unit/infrastructure/posters/test_pillow_renderer.py`. This file is
only about whether the cog wires the interaction to them correctly.
"""

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from stalbot.application.dto.poster_spec import PosterSpec
from stalbot.domain.enums import PosterKind
from stalbot.presentation.cogs.posters import PostersCog


def _spec() -> PosterSpec:
    return PosterSpec(
        title="Продажа бустов",
        logo_path=Path("logo.png"),
        sections=(),
        blocks_per_row=3,
        logo_position=0,
        logo_width=1,
    )


def _cog(*, posters: MagicMock | None = None, renderer: MagicMock | None = None) -> PostersCog:
    posters = posters or MagicMock()
    posters.build = AsyncMock(return_value=_spec())
    renderer = renderer or MagicMock()
    renderer.render = MagicMock(return_value=b"fake-png-bytes")
    return PostersCog(posters, renderer)


def _interaction() -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


async def test_poster_builds_and_posts_the_chosen_kind() -> None:
    posters = MagicMock()
    posters.build = AsyncMock(return_value=_spec())
    cog = _cog(posters=posters)
    interaction = _interaction()

    callback: Any = PostersCog.poster.callback
    await callback(cog, interaction, PosterKind.BOOSTS.value)

    posters.build.assert_awaited_once_with(PosterKind.BOOSTS)
    interaction.followup.send.assert_awaited_once()
    file = interaction.followup.send.call_args.kwargs["file"]
    assert isinstance(file, discord.File)
    # Filename matches the poster's real title (e.g. "Скуп ресурсов.png"),
    # not the internal PosterKind enum value.
    assert file.filename == "Продажа бустов.png"


async def test_poster_renders_the_spec_from_the_service() -> None:
    renderer = MagicMock()
    renderer.render = MagicMock(return_value=b"fake-png-bytes")
    cog = _cog(renderer=renderer)
    interaction = _interaction()

    callback: Any = PostersCog.poster.callback
    await callback(cog, interaction, PosterKind.RESOURCES.value)

    renderer.render.assert_called_once()
