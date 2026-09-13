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
from stalbot.application.services.poster_layout import PosterEntry, PosterOverview
from stalbot.domain.entities.poster_layout import PosterSectionRow, PosterSlotRow
from stalbot.domain.enums import PosterKind
from stalbot.presentation.cogs.posters import PostersCog
from stalbot.presentation.embeds.factory import EmbedFactory


def _spec() -> PosterSpec:
    return PosterSpec(
        title="Продажа бустов",
        logo_path=Path("logo.png"),
        sections=(),
        blocks_per_row=3,
        logo_position=0,
        logo_width=1,
    )


def _cog(
    *,
    posters: MagicMock | None = None,
    renderer: MagicMock | None = None,
    layout: MagicMock | None = None,
) -> PostersCog:
    posters = posters or MagicMock()
    posters.build = AsyncMock(return_value=_spec())
    renderer = renderer or MagicMock()
    renderer.render = MagicMock(return_value=b"fake-png-bytes")
    return PostersCog(posters, renderer, layout or MagicMock(), MagicMock(), EmbedFactory())


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


# -- /poster_item (заявка 13.09.2026 п.6) ----------------------------------


def _slot(**overrides: object) -> PosterSlotRow:
    defaults: dict[str, object] = {
        "id": 1,
        "section_id": 1,
        "sort_order": 2,
        "display_name": "Морфин",
        "name_norm": "морфин",
        "icon_file": "морфин-abc123.png",
        "catalog_item_id": 7,
    }
    defaults.update(overrides)
    return PosterSlotRow(**defaults)  # type: ignore[arg-type]


def _attachment(data: bytes = b"png-bytes") -> MagicMock:
    attachment = MagicMock(spec=discord.Attachment)
    attachment.read = AsyncMock(return_value=data)
    return attachment


async def test_poster_item_add_forwards_the_uploaded_bytes() -> None:
    layout = MagicMock()
    layout.add_item = AsyncMock(return_value=_slot())
    cog = _cog(layout=layout)
    interaction = _interaction()
    attachment = _attachment(b"the-picture")

    callback: Any = PostersCog.poster_item_add.callback
    await callback(cog, interaction, PosterKind.BOOSTS.value, 7, attachment, "Медицина")

    layout.add_item.assert_awaited_once()
    args, kwargs = layout.add_item.call_args
    assert args == (PosterKind.BOOSTS, 7)
    assert kwargs["section_name"] == "Медицина"
    assert kwargs["icon_data"] == b"the-picture"


async def test_poster_item_add_reports_what_it_did() -> None:
    layout = MagicMock()
    layout.add_item = AsyncMock(return_value=_slot())
    cog = _cog(layout=layout)
    interaction = _interaction()

    callback: Any = PostersCog.poster_item_add.callback
    await callback(cog, interaction, PosterKind.BOOSTS.value, 7, _attachment(), "Медицина")

    embed = interaction.followup.send.call_args.kwargs["embed"]
    description = embed.description or ""
    assert "Морфин" in description
    assert "Медицина" in description


async def test_poster_item_icon_replaces_the_picture() -> None:
    layout = MagicMock()
    layout.set_icon = AsyncMock(return_value=_slot(icon_file="морфин-def456.png"))
    cog = _cog(layout=layout)
    interaction = _interaction()

    callback: Any = PostersCog.poster_item_icon.callback
    await callback(cog, interaction, PosterKind.BOOSTS.value, 7, _attachment(b"new"))

    layout.set_icon.assert_awaited_once()
    _args, kwargs = layout.set_icon.call_args
    assert kwargs["icon_data"] == b"new"
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "морфин-def456.png" in (embed.description or "")


async def test_poster_item_move_passes_section_and_position() -> None:
    layout = MagicMock()
    layout.move_item = AsyncMock(return_value=_slot(sort_order=0))
    cog = _cog(layout=layout)
    interaction = _interaction()

    callback: Any = PostersCog.poster_item_move.callback
    await callback(cog, interaction, PosterKind.BOOSTS.value, 7, "Прочее", 1)

    _args, kwargs = layout.move_item.call_args
    assert kwargs["section_name"] == "Прочее"
    assert kwargs["position"] == 1
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "Место: 1" in (embed.description or "")


async def test_poster_item_remove_says_the_catalog_is_untouched() -> None:
    """The distinction matters: this is not `/del_item`."""
    layout = MagicMock()
    layout.remove_item = AsyncMock(return_value=_slot())
    cog = _cog(layout=layout)
    interaction = _interaction()

    callback: Any = PostersCog.poster_item_remove.callback
    await callback(cog, interaction, PosterKind.BOOSTS.value, 7)

    layout.remove_item.assert_awaited_once_with(PosterKind.BOOSTS, 7)
    embed = interaction.followup.send.call_args.kwargs["embed"]
    assert "не** удалён" in (embed.description or "")


async def test_poster_item_list_shows_slots_and_what_is_missing() -> None:
    layout = MagicMock()
    layout.overview = AsyncMock(
        return_value=PosterOverview(
            entries=(
                PosterEntry(
                    slot=_slot(),
                    section=PosterSectionRow(
                        id=1, poster_kind=PosterKind.BOOSTS, name="Медицина", sort_order=0
                    ),
                    item=None,
                    icon_present=True,
                ),
            ),
            missing_from_poster=(),
        )
    )
    cog = _cog(layout=layout)
    interaction = _interaction()

    callback: Any = PostersCog.poster_item_list.callback
    await callback(cog, interaction, PosterKind.BOOSTS.value)

    embed = interaction.followup.send.call_args.kwargs["embed"]
    description = embed.description or ""
    assert "Медицина" in description
    assert "Морфин" in description
    # The slot resolves to nothing in the catalog — it will not draw, and
    # the listing has to say so rather than look healthy.
    assert "нет в каталоге" in description
