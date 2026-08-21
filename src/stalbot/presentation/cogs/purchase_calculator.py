"""`/skupka` — the daily скупка calculator (sqlite_migration.md §I.9, Э8, часть 2).

Replaces the "Кол-во"/"Цена зк" columns on «Мейн скуп»/«БУСТЫ»/«Скуп бустов»
(§I.9): the owner pastes counts by hand many times a day, sees a live total,
and resets everything with one click. Backed by the same `boost_order_lines`
table as the boost-order ticket editor (`BoostOrderService`), keyed by
channel id — but unlike that editor, this has no per-line `Select` (Discord
hard-caps one at 25 options; the owner needs 100+ positions per count,
sqlite_migration.md §I.9), so every mutation goes through
`BoostOrderService.apply_bulk_entry` instead, including single-line
corrections and deletions (`Название x0`).

Не персональный: the draft is shared per channel, matching the owner's
confirmed workflow of never counting concurrently with a co-admin (§I.9).

Известное упрощение (часть 2 first cut): every action posts a fresh card
message rather than editing one tracked in place, and a paste over Discord's
~4000-char modal field limit has no `.txt`-attachment fallback yet — both
are follow-ups, not blockers, since the underlying service methods already
support arbitrarily many lines.
"""

from collections.abc import Awaitable, Callable

import discord
from discord import app_commands
from discord.ext import commands

from stalbot.application.dto.bulk_entry_report import BulkEntryReport
from stalbot.application.services.boost_orders import BoostOrderService
from stalbot.presentation.checks import admin_only
from stalbot.presentation.cogs.calculator_card import render_calculator_body
from stalbot.presentation.embeds.factory import EmbedFactory
from stalbot.presentation.views.error_modal import ErrorReportingModal

_ButtonHandler = Callable[[discord.Interaction], Awaitable[None]]
_BulkSubmitHandler = Callable[[discord.Interaction, str], Awaitable[None]]

_TITLE = "🧮 Калькулятор скупки"
_ENTRIES_MAX_LENGTH = 4000
_ENTRIES_PLACEHOLDER = "Аптечка проводника x12\nКристалл x5\nТопот x0  ← удаляет строку"


class BulkEntryModal(ErrorReportingModal):
    """The one `📋 Вставить список` field — one "Название xКоличество" per line."""

    def __init__(self, on_submit: _BulkSubmitHandler, *, embeds: EmbedFactory) -> None:
        """Build the modal.

        Args:
            on_submit: Called with the interaction and the raw pasted text
                (parsed by the caller via `BoostOrderService.apply_bulk_entry`).
            embeds: Factory used to build the error embed on an `on_submit` failure.
        """
        super().__init__(title="📋 Вставить список", embeds=embeds)
        self._on_submit_cb = on_submit
        self.entries: discord.ui.TextInput[BulkEntryModal] = discord.ui.TextInput(
            label="Название xКоличество, по одному на строку",
            style=discord.TextStyle.paragraph,
            placeholder=_ENTRIES_PLACEHOLDER,
            max_length=_ENTRIES_MAX_LENGTH,
        )
        self.add_item(self.entries)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Forward the pasted text to the injected handler."""
        await self._on_submit_cb(interaction, str(self.entries.value))


class CalculatorView(discord.ui.View):
    """The calculator card's persistent controls: bulk-entry + reset."""

    def __init__(self, *, on_bulk: _ButtonHandler, on_reset: _ButtonHandler) -> None:
        """Build the view.

        Args:
            on_bulk: `📋 Вставить список` — opens `BulkEntryModal`.
            on_reset: `🔄 Сбросить` — clears every line for the channel,
                mirroring the sheet's red reset button (§I.9).
        """
        super().__init__(timeout=None)
        self.add_item(_Button("📋 Вставить список", "calc:bulk", on_bulk))
        self.add_item(
            _Button("🔄 Сбросить", "calc:reset", on_reset, style=discord.ButtonStyle.danger)
        )


class _Button(discord.ui.Button["CalculatorView"]):
    def __init__(
        self,
        label: str,
        custom_id: str,
        handler: _ButtonHandler,
        *,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
    ) -> None:
        super().__init__(label=label, style=style, custom_id=custom_id)
        self._handler = handler

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._handler(interaction)


class PurchaseCalculatorCog(commands.Cog):
    """`/skupka` and its card's button handlers."""

    def __init__(self, boost_orders: BoostOrderService, embeds: EmbedFactory) -> None:
        """Wire the cog to the service it delegates to.

        Args:
            boost_orders: Draft-line CRUD, bulk-entry parsing, live pricing —
                shared with the boost-order ticket editor.
            embeds: Builds every embed this cog sends.
        """
        self._orders = boost_orders
        self._embeds = embeds

    def persistent_views(self) -> tuple[discord.ui.View, ...]:
        """The one persistent View this cog owns, for `bot.add_view()` at startup."""
        return (self._build_view(),)

    @app_commands.command(name="skupka", description="🛡️ [Админ] 🧮 Калькулятор скупки")
    @admin_only()
    async def skupka(self, interaction: discord.Interaction) -> None:
        """Handle `/skupka`: post a fresh calculator card for this channel."""
        channel_id = interaction.channel_id or 0
        embed = await self._render(channel_id)
        await interaction.response.send_message(embed=embed, view=self._build_view())

    async def _on_bulk_button(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            BulkEntryModal(self._on_bulk_submitted, embeds=self._embeds)
        )

    async def _on_bulk_submitted(self, interaction: discord.Interaction, text: str) -> None:
        channel_id = interaction.channel_id or 0
        report = await self._orders.apply_bulk_entry(channel_id, text)
        await interaction.response.defer(ephemeral=True)
        channel = interaction.channel
        if isinstance(channel, discord.abc.Messageable):
            await channel.send(embed=await self._render(channel_id), view=self._build_view())
        await interaction.followup.send(embed=self._report_embed(report), ephemeral=True)

    async def _on_reset_button(self, interaction: discord.Interaction) -> None:
        channel_id = interaction.channel_id or 0
        removed_count = len(await self._orders.list_lines(channel_id))
        await self._orders.clear(channel_id)
        await interaction.response.send_message(
            embed=await self._render(channel_id), view=self._build_view()
        )
        await interaction.followup.send(
            embed=self._embeds.success("🔄 Сброшено", f"Удалено позиций: {removed_count}."),
            ephemeral=True,
        )

    async def _render(self, channel_id: int) -> discord.Embed:
        lines_with_items = await self._orders.list_lines_with_items(channel_id)
        total = await self._orders.compute_total(channel_id)
        return self._embeds.info(_TITLE, render_calculator_body(lines_with_items, total))

    def _report_embed(self, report: BulkEntryReport) -> discord.Embed:
        if not (report.not_parsed or report.not_found or report.ambiguous or report.removed):
            resolved = len(report.applied)
            return self._embeds.success("✅ Список обработан", f"Строк применено: {resolved}.")
        sections: list[str] = []
        if report.applied or report.removed:
            sections.append(
                f"✅ Применено: {len(report.applied)} • 🗑️ Удалено: {len(report.removed)}"
            )
        if report.ambiguous:
            sections.append(
                "⚠️ Неоднозначно — есть и как покупка, и как продажа, "
                "уточните вручную:\n" + "\n".join(report.ambiguous)
            )
        if report.not_found:
            sections.append("❓ Не найдено в каталоге:\n" + "\n".join(report.not_found))
        if report.not_parsed:
            sections.append(
                "🚫 Не распознан формат «Название xКоличество»:\n" + "\n".join(report.not_parsed)
            )
        return self._embeds.warning("⚠️ Есть строки, требующие внимания", "\n\n".join(sections))

    def _build_view(self) -> CalculatorView:
        return CalculatorView(on_bulk=self._on_bulk_button, on_reset=self._on_reset_button)
