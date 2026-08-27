"""`/coupon_add`, `/coupon_disable`, `/coupon_delete`, `/coupons` — admin coupon management.

заявка 26.08+27.08.2026: full CRUD from Discord, no code edits needed —
`/coupons` lists every active coupon with a picker + edit/delete buttons,
mirroring the boost-order editor's select-then-act pattern.
"""

from collections.abc import Awaitable, Callable, Sequence
from decimal import Decimal, InvalidOperation
from typing import Final

import discord
from discord import app_commands
from discord.ext import commands

from stalbot.application.services.coupons import CouponService
from stalbot.domain.clock import SystemClock, format_datetime, parse_deadline
from stalbot.domain.entities.coupon import Coupon
from stalbot.domain.enums import CouponKind
from stalbot.domain.errors import CouponNotFoundError, DeadlineParseError
from stalbot.presentation.checks import admin_only
from stalbot.presentation.embeds.factory import EmbedFactory, enforce_limits
from stalbot.presentation.views.base import AuthorLockedView
from stalbot.presentation.views.confirm import ConfirmView
from stalbot.presentation.views.error_modal import ErrorReportingModal

_KIND_LABEL: Final[dict[CouponKind, str]] = {
    CouponKind.DISCOUNT: "📉 Скидка (заказ бустов)",
    CouponKind.MARKUP: "📈 Наценка (скупка)",
}
_MAX_LISTED: Final = 25

_EditSubmitHandler = Callable[
    [discord.Interaction, str, str, str | None, str | None], Awaitable[None]
]


def _coupon_lines(coupon: Coupon) -> list[str]:
    uses = f"🔢 Использован: {coupon.used_count}"
    if coupon.max_uses:
        uses += f" / {coupon.max_uses}"
    lines = [f"{_KIND_LABEL[coupon.kind]}", f"📊 Процент: {coupon.discount_percent}%", uses]
    if coupon.expires_at is not None:
        lines.append(f"⏳ До: {format_datetime(coupon.expires_at)}")
    if coupon.created_by is not None:
        lines.append(f"👤 Создал: <@{coupon.created_by}>")
    return lines


class _CouponEditModal(ErrorReportingModal):
    """Pre-filled скидка/лимит/срок for one coupon (`/coupons`' ✏️ button)."""

    def __init__(
        self, coupon: Coupon, on_submit: _EditSubmitHandler, *, embeds: EmbedFactory
    ) -> None:
        super().__init__(title=f"✏️ {coupon.code}", embeds=embeds)
        self._on_submit_cb = on_submit
        self._code = coupon.code
        self.percent: discord.ui.TextInput[_CouponEditModal] = discord.ui.TextInput(
            label="Процент", default=str(coupon.discount_percent), max_length=8
        )
        self.max_uses: discord.ui.TextInput[_CouponEditModal] = discord.ui.TextInput(
            label="Лимит использований (пусто — без лимита)",
            required=False,
            default=str(coupon.max_uses) if coupon.max_uses is not None else None,
            max_length=8,
        )
        self.expires: discord.ui.TextInput[_CouponEditModal] = discord.ui.TextInput(
            label="До какой даты (пусто — без срока)",
            required=False,
            default=format_datetime(coupon.expires_at) if coupon.expires_at is not None else None,
            max_length=50,
        )
        self.add_item(self.percent)
        self.add_item(self.max_uses)
        self.add_item(self.expires)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._on_submit_cb(
            interaction,
            self._code,
            str(self.percent.value),
            str(self.max_uses.value) or None,
            str(self.expires.value) or None,
        )


class _CouponsView(AuthorLockedView):
    """Select-a-coupon-then-act, same shape as the boost-order editor."""

    def __init__(
        self,
        coupons: Sequence[Coupon],
        *,
        author_id: int,
        embeds: EmbedFactory,
        on_edit: Callable[[discord.Interaction, Coupon], Awaitable[None]],
        on_delete: Callable[[discord.Interaction, Coupon], Awaitable[None]],
    ) -> None:
        super().__init__(author_id=author_id, timeout=180.0)
        self._coupons = {c.code: c for c in coupons[:_MAX_LISTED]}
        self._embeds = embeds
        self._on_edit = on_edit
        self._on_delete = on_delete
        self._selected: str | None = None

        self._select: discord.ui.Select[_CouponsView] = discord.ui.Select(
            placeholder="Выберите купон",
            options=[
                discord.SelectOption(
                    label=(
                        f"{c.code} — {c.discount_percent}% "
                        f"({_KIND_LABEL[c.kind].split()[0]})"
                    ),
                    value=c.code,
                )
                for c in self._coupons.values()
            ]
            or [discord.SelectOption(label="Нет активных купонов", value="none")],
        )
        self._select.disabled = not self._coupons
        self._select.callback = self._handle_select  # type: ignore[method-assign]
        self.add_item(self._select)

    @property
    def embed(self) -> discord.Embed:
        if not self._coupons:
            return self._embeds.info("🎟️ Активные купоны", "Пока нет ни одного активного купона.")
        embed = self._embeds.info("🎟️ Активные купоны", f"Всего: {len(self._coupons)}.")
        for coupon in self._coupons.values():
            embed.add_field(name=coupon.code, value="\n".join(_coupon_lines(coupon)), inline=True)
        return enforce_limits(embed)

    async def _handle_select(self, interaction: discord.Interaction) -> None:
        if self._select.values and self._select.values[0] != "none":
            self._selected = self._select.values[0]
        await interaction.response.defer()

    @discord.ui.button(label="✏️ Редактировать", style=discord.ButtonStyle.primary, row=1)
    async def edit(
        self, interaction: discord.Interaction, _button: discord.ui.Button["_CouponsView"]
    ) -> None:
        coupon = self._coupons.get(self._selected or "")
        if coupon is None:
            embed = self._embeds.warning("⚠️ Ничего не выбрано", "Сначала выберите купон в списке.")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        await self._on_edit(interaction, coupon)

    @discord.ui.button(label="🗑️ Удалить", style=discord.ButtonStyle.danger, row=1)
    async def delete(
        self, interaction: discord.Interaction, _button: discord.ui.Button["_CouponsView"]
    ) -> None:
        coupon = self._coupons.get(self._selected or "")
        if coupon is None:
            embed = self._embeds.warning("⚠️ Ничего не выбрано", "Сначала выберите купон в списке.")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        await self._on_delete(interaction, coupon)


class CouponsCog(commands.Cog):
    """`/coupon_add`, `/coupon_disable`, `/coupon_delete`, `/coupons`."""

    def __init__(self, coupons: CouponService, embeds: EmbedFactory) -> None:
        """Wire the cog to the service it delegates to.

        Args:
            coupons: Creates/edits/disables/deletes coupons.
            embeds: Builds every embed this cog sends.
        """
        self._coupons = coupons
        self._embeds = embeds

    @app_commands.command(name="coupon_add", description="🛡️ [Админ] 🎟️ Создать промокод")
    @app_commands.describe(
        тип="Скидка (заказ бустов) или наценка (скупка)",
        код="Код, который будут вводить игроки",
        процент="Процент, например 1.5",
        макс_использований="Сколько раз всего можно применить (опционально — без лимита)",
        до="До какой даты и времени действует (опционально — без срока)",
    )
    @app_commands.choices(
        тип=[
            app_commands.Choice(
                name=_KIND_LABEL[CouponKind.DISCOUNT], value=CouponKind.DISCOUNT.value
            ),
            app_commands.Choice(
                name=_KIND_LABEL[CouponKind.MARKUP], value=CouponKind.MARKUP.value
            ),
        ]
    )
    @admin_only()
    async def coupon_add(
        self,
        interaction: discord.Interaction,
        тип: app_commands.Choice[str],
        код: str,
        процент: str,
        макс_использований: int | None = None,
        до: str | None = None,
    ) -> None:
        """Handle `/coupon_add`: create a new active coupon."""
        await interaction.response.defer(ephemeral=True)
        try:
            discount_percent = Decimal(процент.replace(",", "."))
        except InvalidOperation:
            await self._send_error(interaction, "Процент должен быть числом, например 1.5.")
            return
        if not (Decimal(0) < discount_percent <= Decimal(100)):
            await self._send_error(interaction, "Процент должен быть от 0 до 100.")
            return

        expires_at = None
        if до:
            try:
                expires_at = parse_deadline(до, now=SystemClock().now())
            except DeadlineParseError:
                await self._send_error(interaction, "Не удалось распознать дату.")
                return

        coupon = await self._coupons.create(
            код,
            CouponKind(тип.value),
            discount_percent,
            max_uses=макс_использований,
            expires_at=expires_at,
            created_by=interaction.user.id,
        )
        embed = self._embeds.success(
            "✅ Промокод создан", "\n".join([f"🎟️ Код: {coupon.code}", *_coupon_lines(coupon)])
        )
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
            await self._send_error(interaction, "Купон с таким кодом не найден.")
            return
        embed = self._embeds.success(
            "🚫 Промокод отключён", f"«{coupon.code}» больше не действует."
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="coupon_delete", description="🛡️ [Админ] 🗑️ Удалить промокод навсегда"
    )
    @app_commands.describe(код="Код промокода")
    @admin_only()
    async def coupon_delete(self, interaction: discord.Interaction, код: str) -> None:
        """Handle `/coupon_delete`: permanently remove a coupon after confirmation."""
        coupon = await self._coupons.get(код)
        if coupon is None:
            await interaction.response.defer(ephemeral=True)
            await self._send_error(interaction, "Купон с таким кодом не найден.")
            return
        await self._confirm_and_delete(interaction, coupon)

    @app_commands.command(name="coupons", description="🛡️ [Админ] 🎟️ Список активных промокодов")
    @admin_only()
    async def coupons(self, interaction: discord.Interaction) -> None:
        """Handle `/coupons`: list every active coupon with an edit/delete picker."""
        await interaction.response.defer(ephemeral=True)
        active = await self._coupons.list_active()
        view = _CouponsView(
            active,
            author_id=interaction.user.id,
            embeds=self._embeds,
            on_edit=self._on_edit_selected,
            on_delete=self._confirm_and_delete,
        )
        message = await interaction.followup.send(
            embed=view.embed, view=view, ephemeral=True, wait=True
        )
        view.message = message

    async def _on_edit_selected(self, interaction: discord.Interaction, coupon: Coupon) -> None:
        await interaction.response.send_modal(
            _CouponEditModal(coupon, self._on_edit_submitted, embeds=self._embeds)
        )

    async def _on_edit_submitted(
        self,
        interaction: discord.Interaction,
        code: str,
        percent_text: str,
        max_uses_text: str | None,
        expires_text: str | None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            discount_percent = Decimal(percent_text.replace(",", "."))
        except InvalidOperation:
            await self._send_error(interaction, "Процент должен быть числом, например 1.5.")
            return
        max_uses = None
        if max_uses_text:
            try:
                max_uses = int(max_uses_text)
            except ValueError:
                await self._send_error(interaction, "Лимит использований должен быть целым числом.")
                return
        expires_at = None
        if expires_text:
            try:
                expires_at = parse_deadline(expires_text, now=SystemClock().now())
            except DeadlineParseError:
                await self._send_error(interaction, "Не удалось распознать дату.")
                return

        coupon = await self._coupons.update(
            code, discount_percent=discount_percent, max_uses=max_uses, expires_at=expires_at
        )
        embed = self._embeds.success(
            "✅ Промокод обновлён", "\n".join([f"🎟️ Код: {coupon.code}", *_coupon_lines(coupon)])
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def _confirm_and_delete(self, interaction: discord.Interaction, coupon: Coupon) -> None:
        """Shared by `/coupon_delete` and `/coupons`' 🗑️ button — confirm, then delete."""
        embed = self._embeds.warning(
            "⚠️ Удалить промокод?", f"«{coupon.code}» будет удалён без возможности восстановить."
        )
        view = ConfirmView(author_id=interaction.user.id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        message = await interaction.original_response()
        view.message = message
        await view.wait()
        if not view.confirmed:
            await interaction.followup.send(
                embed=self._embeds.info("Отменено", "Промокод не был удалён."), ephemeral=True
            )
            return
        await self._coupons.delete(coupon.code)
        await interaction.followup.send(
            embed=self._embeds.success("🗑️ Промокод удалён", f"«{coupon.code}» удалён."),
            ephemeral=True,
        )

    async def _send_error(self, interaction: discord.Interaction, message: str) -> None:
        embed = self._embeds.error("Ошибка", message)
        await interaction.followup.send(embed=embed, ephemeral=True)
