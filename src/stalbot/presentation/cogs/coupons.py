"""`/coupon_add`, `/coupon_disable` — admin coupon management (заявка 26.08.2026)."""

from decimal import Decimal, InvalidOperation

import discord
from discord import app_commands
from discord.ext import commands

from stalbot.application.services.coupons import CouponService
from stalbot.domain.clock import SystemClock, format_datetime, parse_deadline
from stalbot.domain.errors import CouponNotFoundError, DeadlineParseError
from stalbot.presentation.checks import admin_only
from stalbot.presentation.embeds.factory import EmbedFactory


class CouponsCog(commands.Cog):
    """`/coupon_add` and `/coupon_disable`."""

    def __init__(self, coupons: CouponService, embeds: EmbedFactory) -> None:
        """Wire the cog to the service it delegates to.

        Args:
            coupons: Creates/disables coupons.
            embeds: Builds every embed this cog sends.
        """
        self._coupons = coupons
        self._embeds = embeds

    @app_commands.command(name="coupon_add", description="🛡️ [Админ] 🎟️ Создать промокод")
    @app_commands.describe(
        код="Код, который будут вводить игроки",
        скидка="Процент скидки, например 1.5",
        макс_использований="Сколько раз всего можно применить (опционально — без лимита)",
        до="До какой даты и времени действует (опционально — без срока)",
    )
    @admin_only()
    async def coupon_add(
        self,
        interaction: discord.Interaction,
        код: str,
        скидка: str,
        макс_использований: int | None = None,
        до: str | None = None,
    ) -> None:
        """Handle `/coupon_add`: create a new active coupon."""
        await interaction.response.defer(ephemeral=True)
        try:
            discount_percent = Decimal(скидка.replace(",", "."))
        except InvalidOperation:
            embed = self._embeds.error("Ошибка", "Скидка должна быть числом, например 1.5.")
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        if not (Decimal(0) < discount_percent <= Decimal(100)):
            embed = self._embeds.error("Ошибка", "Скидка должна быть от 0 до 100%.")
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        expires_at = None
        if до:
            try:
                expires_at = parse_deadline(до, now=SystemClock().now())
            except DeadlineParseError:
                embed = self._embeds.error("Ошибка", "Не удалось распознать дату.")
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

        coupon = await self._coupons.create(
            код,
            discount_percent,
            max_uses=макс_использований,
            expires_at=expires_at,
            created_by=interaction.user.id,
        )

        lines = [f"🎟️ Код: {coupon.code}", f"📉 Скидка: {coupon.discount_percent}%"]
        if coupon.max_uses is not None:
            lines.append(f"🔢 Лимит использований: {coupon.max_uses}")
        if coupon.expires_at is not None:
            lines.append(f"⏳ Действует до: {format_datetime(coupon.expires_at)}")
        embed = self._embeds.success("✅ Промокод создан", "\n".join(lines))
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="coupon_disable", description="🛡️ [Админ] 🚫 Отключить промокод")
    @app_commands.describe(код="Код промокода")
    @admin_only()
    async def coupon_disable(self, interaction: discord.Interaction, код: str) -> None:
        """Handle `/coupon_disable`: deactivate a coupon without deleting its history."""
        await interaction.response.defer(ephemeral=True)
        try:
            coupon = await self._coupons.disable(код)
        except CouponNotFoundError:
            embed = self._embeds.error("Ошибка", "Купон с таким кодом не найден.")
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        embed = self._embeds.success("🚫 Промокод отключён", f"«{coupon.code}» больше не действует.")
        await interaction.followup.send(embed=embed, ephemeral=True)
