"""`/setprice`, `/setboost`, `/sync_prices`, `/new_price` — price maintenance.

PLAN.md §10.6-§10.8.
"""

from collections.abc import Sequence
from typing import Final

import discord
from discord import app_commands
from discord.ext import commands

from stalbot.application.dto.price_change import PriceChange
from stalbot.application.services.pricing import (
    PricingService,
    decode_price_list_bytes,
    render_price_change_report,
)
from stalbot.config.settings import Settings
from stalbot.domain.enums import ItemCategory, PriceField
from stalbot.domain.money import evaluate_amount
from stalbot.infrastructure.cache.repositories.items import ItemsCacheRepository
from stalbot.presentation.autocomplete import item_choices
from stalbot.presentation.checks import admin_only
from stalbot.presentation.embeds.factory import EmbedFactory
from stalbot.presentation.views.confirm import ConfirmView

_MAX_IMPORT_FILE_BYTES: Final = 1_000_000
_NOT_FOUND_PREVIEW_LIMIT: Final = 10

#: SEC-5: defense-in-depth per-admin cooldown on the two commands that scan
#: every price sheet / parse-and-apply a bulk import — not a response to any
#: observed abuse, just a cheap guard against an accidental double-click or
#: a careless script hammering an already-privileged session.
_HEAVY_COMMAND_COOLDOWN_SECONDS: Final = 15.0


class PricingCog(commands.Cog):
    """`/setprice`, `/setboost`, `/sync_prices`, `/new_price`."""

    def __init__(
        self,
        pricing: PricingService,
        items: ItemsCacheRepository,
        embeds: EmbedFactory,
        settings: Settings,
    ) -> None:
        """Wire the cog to the service it delegates to.

        Args:
            pricing: Reads/writes item prices and the price sheets.
            items: Read-only lookup, for the shared report and autocomplete.
            embeds: Builds every embed this cog sends.
            settings: For `PRICE_IMPORT_CONFIRM`.
        """
        self._pricing = pricing
        self._items = items
        self._embeds = embeds
        self._settings = settings

    @app_commands.command(
        name="setprice", description="🛡️ [Админ] 💲 Установить цену скупки ресурса"
    )
    @app_commands.describe(предмет="Название ресурса", цена="Новая цена, например 250000 или 250к")
    @admin_only()
    async def setprice(self, interaction: discord.Interaction, предмет: int, цена: str) -> None:
        """Handle `/setprice`: set a resource's скуп (buy) price."""
        await interaction.response.defer(ephemeral=True)
        amount = evaluate_amount(цена)
        change = await self._pricing.set_price(предмет, PriceField.BUY, amount)
        await self._send_change_report(interaction, [change])

    @setprice.autocomplete("предмет")
    async def _setprice_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return item_choices(await self._items.by_category(ItemCategory.RESOURCE), current)

    @app_commands.command(name="setboost", description="🛡️ [Админ] 💲 Установить цену продажи буста")
    @app_commands.describe(буст="Название буста", цена="Новая цена, например 300000 или 300к")
    @admin_only()
    async def setboost(self, interaction: discord.Interaction, буст: int, цена: str) -> None:
        """Handle `/setboost`: set a boost's продажа (sell) price."""
        await interaction.response.defer(ephemeral=True)
        amount = evaluate_amount(цена)
        change = await self._pricing.set_price(буст, PriceField.SELL, amount)
        await self._send_change_report(interaction, [change])

    @setboost.autocomplete("буст")
    async def _setboost_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return item_choices(await self._items.by_category(ItemCategory.BOOST), current)

    @app_commands.command(
        name="sync_prices", description="🛡️ [Админ] 🔄 Синхронизировать цены с прайс-листов"
    )
    @app_commands.checks.cooldown(1, _HEAVY_COMMAND_COOLDOWN_SECONDS)
    @admin_only()
    async def sync_prices(self, interaction: discord.Interaction) -> None:
        """Handle `/sync_prices`: push item-database prices onto every price sheet (UX #7)."""
        await interaction.response.defer(ephemeral=True)
        report = await self._pricing.sync_prices()

        lines = [f"➖ Без изменений: {report.unchanged_count}"]
        if report.not_found:
            names = ", ".join(report.not_found[:_NOT_FOUND_PREVIEW_LIMIT])
            extra = len(report.not_found) - _NOT_FOUND_PREVIEW_LIMIT
            if extra > 0:
                names += f" и ещё {extra}"
            lines.append(f"⚠️ Не найдено в базе: {names}")
        if report.unparseable:
            names = ", ".join(report.unparseable[:_NOT_FOUND_PREVIEW_LIMIT])
            extra = len(report.unparseable) - _NOT_FOUND_PREVIEW_LIMIT
            if extra > 0:
                names += f" и ещё {extra}"
            lines.append(f"❌ Была нечитаемая цена в ячейке (перезаписано): {names}")

        if report.updated:
            catalog = await self._items.all()
            diff_text = render_price_change_report(report.updated, catalog)
            description = f"{diff_text}\n\n" + "\n".join(lines)
        else:
            description = "\n".join(lines)

        embed = self._embeds.success("🔄 Синхронизация цен", description)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="new_price", description="🛡️ [Админ] 📥 Импортировать цены из TXT")
    @app_commands.describe(файл="TXT-файл прайс-листа (формат — как в /give_price)")
    @app_commands.checks.cooldown(1, _HEAVY_COMMAND_COOLDOWN_SECONDS)
    @admin_only()
    async def new_price(self, interaction: discord.Interaction, файл: discord.Attachment) -> None:
        """Handle `/new_price`: parse, validate, show a diff, then apply on confirmation."""
        await interaction.response.defer(ephemeral=True)

        if not файл.filename.lower().endswith(".txt"):
            await self._send_error(interaction, "Файл должен быть в формате .txt.")
            return
        if файл.size > _MAX_IMPORT_FILE_BYTES:
            await self._send_error(interaction, "Файл слишком большой (максимум 1 МБ).")
            return

        try:
            text = decode_price_list_bytes(await файл.read())
        except UnicodeDecodeError:
            await self._send_error(
                interaction, "Не удалось определить кодировку файла (ожидается UTF-8 или CP1251)."
            )
            return

        plan = await self._pricing.preview_import(text)
        if not plan.is_valid:
            lines = [f"Строка {issue.line_number}: {issue.message}" for issue in plan.issues]
            embed = self._embeds.error("❌ Ошибки в файле", "\n".join(lines))
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        if not plan.changes:
            embed = self._embeds.info("ℹ️ Изменений нет", "Все цены в файле совпадают с текущими.")
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        catalog = await self._items.all()
        report_text = render_price_change_report(plan.changes, catalog)

        if not self._settings.price_import_confirm:
            await self._pricing.apply_import(plan)
            embed = self._embeds.success("✅ Цены обновлены", report_text)
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        confirm_embed = self._embeds.info("✏️ Подтвердите изменение цен", report_text)
        view = ConfirmView(author_id=interaction.user.id)
        message = await interaction.followup.send(
            embed=confirm_embed, view=view, ephemeral=True, wait=True
        )
        view.message = message
        await view.wait()
        if not view.confirmed:
            cancel_embed = self._embeds.info("Отменено", "Цены не были изменены.")
            await interaction.followup.send(embed=cancel_embed, ephemeral=True)
            return

        await self._pricing.apply_import(plan)
        done_embed = self._embeds.success("✅ Цены обновлены", report_text)
        await interaction.followup.send(embed=done_embed, ephemeral=True)

    async def _send_change_report(
        self, interaction: discord.Interaction, changes: Sequence[PriceChange]
    ) -> None:
        catalog = await self._items.all()
        text = render_price_change_report(changes, catalog)
        embed = self._embeds.success("✏️ Изменение цен", text)
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def _send_error(self, interaction: discord.Interaction, message: str) -> None:
        embed = self._embeds.error("Ошибка", message)
        await interaction.followup.send(embed=embed, ephemeral=True)
