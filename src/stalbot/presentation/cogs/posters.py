"""`/poster` and the `/poster_item` group — rendering and editing the catalog posters.

заявка 13.09.2026 п.6 и вторая половина п.13: the layout lives in the
database now (`PosterLayoutService`), so adding an item to a poster is a
command with a screenshot attached instead of a code change. The picture is
attached straight to the slash command — Discord passes an
`discord.Attachment` as a parameter, so there is no "now send the image as
the next message" dance to get wrong.

Known simplification carried over from the first cut (sqlite_migration.md
Э8): `/poster` posts a fresh message every call rather than editing one
tracked message.
"""

import io
from collections.abc import Sequence
from typing import Final

import discord
from discord import app_commands
from discord.ext import commands

from stalbot.application.ports.poster_renderer import PosterRenderer
from stalbot.application.services.poster_layout import (
    PosterEntry,
    PosterLayoutService,
    PosterOverview,
)
from stalbot.application.services.posters import CATEGORY_BY_KIND, PosterService
from stalbot.domain.enums import PosterKind
from stalbot.infrastructure.cache.repositories.catalog_items import CatalogItemsRepository
from stalbot.presentation.autocomplete import item_choices
from stalbot.presentation.checks import admin_only
from stalbot.presentation.embeds.factory import EmbedFactory, enforce_limits
from stalbot.presentation.views.paginated_embed import PaginatedEmbedView

_POSTER_CHOICES: Final = [
    app_commands.Choice(name="Скупка ресурсов", value=PosterKind.RESOURCES.value),
    app_commands.Choice(name="Продажа бустов", value=PosterKind.BOOSTS.value),
    app_commands.Choice(name="Скупка бустов", value=PosterKind.BOOST_PURCHASES.value),
]

_POSTER_TITLE: Final[dict[PosterKind, str]] = {
    PosterKind.RESOURCES: "Скупка ресурсов",
    PosterKind.BOOSTS: "Продажа бустов",
    PosterKind.BOOST_PURCHASES: "Скупка бустов",
}

_LIST_PAGE_SIZE: Final = 20
_MAX_MISSING_SHOWN: Final = 15


class PostersCog(commands.Cog):
    """`/poster <тип>` plus the `/poster_item` editing group."""

    poster_item = app_commands.Group(
        name="poster_item",
        description="🛡️ [Админ] 🖼️ Что показано на плакатах",
    )

    def __init__(
        self,
        posters: PosterService,
        renderer: PosterRenderer,
        layout: PosterLayoutService,
        items: CatalogItemsRepository,
        embeds: EmbedFactory,
    ) -> None:
        """Wire the cog to the services it delegates to.

        Args:
            posters: Resolves a poster kind + live catalog prices into a spec.
            renderer: Draws the spec into PNG bytes.
            layout: Adds/re-pictures/moves/removes slots.
            items: Read-only lookup for autocomplete.
            embeds: Builds every embed this cog sends.
        """
        self._posters = posters
        self._renderer = renderer
        self._layout = layout
        self._items = items
        self._embeds = embeds

    @app_commands.command(name="poster", description="🛡️ [Админ] 🖼️ Плакат каталога")
    @app_commands.describe(тип="Какой плакат сгенерировать")
    @app_commands.choices(тип=_POSTER_CHOICES)
    @admin_only()
    async def poster(self, interaction: discord.Interaction, тип: str) -> None:
        """Handle `/poster`: render the chosen kind and post it as an image."""
        await interaction.response.defer()
        kind = PosterKind(тип)
        spec = await self._posters.build(kind)
        png = self._renderer.render(spec)
        file = discord.File(io.BytesIO(png), filename=f"{spec.title}.png")
        await interaction.followup.send(file=file)

    # -- /poster_item --------------------------------------------------------

    @poster_item.command(name="add", description="➕ Добавить предмет на плакат со скриншотом")
    @app_commands.describe(
        плакат="На какой плакат",
        предмет="Предмет из каталога",
        скриншот="Картинка предмета — PNG, JPEG или WebP",
        секция="Название секции (новая создастся) — необязательно",
    )
    @app_commands.choices(плакат=_POSTER_CHOICES)
    @admin_only()
    async def poster_item_add(
        self,
        interaction: discord.Interaction,
        плакат: str,
        предмет: int,
        скриншот: discord.Attachment,
        секция: str | None = None,
    ) -> None:
        """Handle `/poster_item add`: place an item on a poster with its picture."""
        await interaction.response.defer(ephemeral=True)
        kind = PosterKind(плакат)
        slot = await self._layout.add_item(
            kind,
            предмет,
            section_name=секция,
            icon_data=await скриншот.read(),
        )
        embed = self._embeds.success(
            "✅ Предмет добавлен на плакат",
            f"🖼️ Плакат: {_POSTER_TITLE[kind]}\n"
            f"📦 Предмет: {slot.display_name}\n"
            f"🗂️ Секция: {секция or 'новая без названия'}\n"
            f"🎨 Картинка: {slot.icon_file}\n\n"
            "Плакат перерисуется при следующем `/poster`.",
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @poster_item.command(name="icon", description="🎨 Заменить картинку предмета на плакате")
    @app_commands.describe(
        плакат="Какой плакат",
        предмет="Предмет из каталога",
        скриншот="Новая картинка — PNG, JPEG или WebP",
    )
    @app_commands.choices(плакат=_POSTER_CHOICES)
    @admin_only()
    async def poster_item_icon(
        self,
        interaction: discord.Interaction,
        плакат: str,
        предмет: int,
        скриншот: discord.Attachment,
    ) -> None:
        """Handle `/poster_item icon`: re-picture a slot that already exists."""
        await interaction.response.defer(ephemeral=True)
        kind = PosterKind(плакат)
        slot = await self._layout.set_icon(kind, предмет, icon_data=await скриншот.read())
        embed = self._embeds.success(
            "🎨 Картинка заменена",
            f"🖼️ Плакат: {_POSTER_TITLE[kind]}\n"
            f"📦 Предмет: {slot.display_name}\n"
            f"🎨 Картинка: {slot.icon_file}",
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @poster_item.command(name="move", description="↕️ Переставить предмет на плакате")
    @app_commands.describe(
        плакат="Какой плакат",
        предмет="Предмет из каталога",
        секция="Куда перенести — необязательно, по умолчанию остаётся в своей",
        позиция="Место в секции, начиная с 1 — необязательно, по умолчанию в конец",
    )
    @app_commands.choices(плакат=_POSTER_CHOICES)
    @admin_only()
    async def poster_item_move(
        self,
        interaction: discord.Interaction,
        плакат: str,
        предмет: int,
        секция: str | None = None,
        позиция: int | None = None,
    ) -> None:
        """Handle `/poster_item move`: change a slot's section and/or position."""
        await interaction.response.defer(ephemeral=True)
        kind = PosterKind(плакат)
        slot = await self._layout.move_item(kind, предмет, section_name=секция, position=позиция)
        embed = self._embeds.success(
            "↕️ Позиция изменена",
            f"🖼️ Плакат: {_POSTER_TITLE[kind]}\n"
            f"📦 Предмет: {slot.display_name}\n"
            f"🗂️ Секция: {секция or 'прежняя'}\n"
            f"🔢 Место: {slot.sort_order + 1}",
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @poster_item.command(name="remove", description="➖ Убрать предмет с плаката")
    @app_commands.describe(плакат="Какой плакат", предмет="Предмет из каталога")
    @app_commands.choices(плакат=_POSTER_CHOICES)
    @admin_only()
    async def poster_item_remove(
        self, interaction: discord.Interaction, плакат: str, предмет: int
    ) -> None:
        """Handle `/poster_item remove`: take a slot off, leaving the catalog alone."""
        await interaction.response.defer(ephemeral=True)
        kind = PosterKind(плакат)
        slot = await self._layout.remove_item(kind, предмет)
        embed = self._embeds.success(
            "➖ Убрано с плаката",
            f"🖼️ Плакат: {_POSTER_TITLE[kind]}\n"
            f"📦 Предмет: {slot.display_name}\n\n"
            "Из каталога предмет **не** удалён — только с плаката.",
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @poster_item.command(name="list", description="📋 Что сейчас на плакате")
    @app_commands.describe(плакат="Какой плакат")
    @app_commands.choices(плакат=_POSTER_CHOICES)
    @admin_only()
    async def poster_item_list(self, interaction: discord.Interaction, плакат: str) -> None:
        """Handle `/poster_item list`: the poster's slots, and what is missing from it."""
        await interaction.response.defer(ephemeral=True)
        kind = PosterKind(плакат)
        overview = await self._layout.overview(kind)
        pages = self._build_list_pages(kind, overview)
        if len(pages) == 1:
            await interaction.followup.send(embed=pages[0], ephemeral=True)
            return
        pager = PaginatedEmbedView(pages=pages, author_id=interaction.user.id)
        message = await interaction.followup.send(
            embed=pager.current, view=pager, ephemeral=True, wait=True
        )
        pager.message = message

    def _build_list_pages(self, kind: PosterKind, overview: PosterOverview) -> list[discord.Embed]:
        title = f"📋 {_POSTER_TITLE[kind]}"
        chunks = _chunk(overview.entries, _LIST_PAGE_SIZE) or [()]
        broken = sum(1 for entry in overview.entries if not entry.is_healthy)

        pages: list[discord.Embed] = []
        for index, chunk in enumerate(chunks, start=1):
            page_title = title if len(chunks) == 1 else f"{title} (стр. {index}/{len(chunks)})"
            lines: list[str] = []
            if index == 1:
                lines.append(f"Позиций на плакате: {len(overview.entries)}.")
                if broken:
                    lines.append(f"⚠️ Не отрисуется: {broken} — подробности ниже помечены.")
                lines.append("")
            if not chunk:
                lines.append("Плакат пуст.")
            for entry in chunk:
                lines.append(_entry_line(entry))
            if index == len(chunks) and overview.missing_from_poster:
                lines.append("")
                lines.append(
                    f"📭 В каталоге есть, но на плакате нет ({len(overview.missing_from_poster)}):"
                )
                shown = overview.missing_from_poster[:_MAX_MISSING_SHOWN]
                lines.extend(f" • {item.name}" for item in shown)
                hidden = len(overview.missing_from_poster) - len(shown)
                if hidden > 0:
                    lines.append(f" … и ещё {hidden}")
            pages.append(enforce_limits(self._embeds.info(page_title, "\n".join(lines))))
        return pages

    # -- autocomplete --------------------------------------------------------

    @poster_item_add.autocomplete("предмет")
    @poster_item_icon.autocomplete("предмет")
    @poster_item_move.autocomplete("предмет")
    @poster_item_remove.autocomplete("предмет")
    async def _item_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        """Only items on the side of the trade the chosen poster prices."""
        kind = _chosen_kind(interaction)
        category = CATEGORY_BY_KIND[kind] if kind is not None else None
        return item_choices(await self._items.all(), current, category=category)

    @poster_item_add.autocomplete("секция")
    @poster_item_move.autocomplete("секция")
    async def _section_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Existing section names of the chosen poster — typing a new one still works."""
        kind = _chosen_kind(interaction)
        if kind is None:
            return []
        names = [
            section.name
            for section in await self._layout.sections(kind)
            if section.name is not None
        ]
        query = current.casefold()
        matches = [name for name in names if query in name.casefold()]
        return [app_commands.Choice(name=name, value=name) for name in matches[:25]]


def _chosen_kind(interaction: discord.Interaction) -> PosterKind | None:
    """The `плакат` option as already filled in, for a dependent autocomplete."""
    raw = getattr(interaction.namespace, "плакат", None)
    if not isinstance(raw, str):
        return None
    try:
        return PosterKind(raw)
    except ValueError:
        return None


def _entry_line(entry: PosterEntry) -> str:
    section = entry.section.name or "без названия"
    marks: list[str] = []
    if entry.item is None:
        marks.append("нет в каталоге")
    if not entry.icon_present:
        marks.append("нет картинки")
    if entry.item is not None and entry.item.price_buy is None and entry.item.price_sell is None:
        marks.append("нет цены")
    suffix = f" ⚠️ {', '.join(marks)}" if marks else ""
    return f" • [{section}] {entry.slot.display_name}{suffix}"


def _chunk[T](items: Sequence[T], size: int) -> list[Sequence[T]]:
    return [items[i : i + size] for i in range(0, len(items), size)]
