"""`/resync_roles` — force a right-now rank/referral role reconciliation.

Same reconciliation the background poller already runs every few minutes,
triggered on demand: for when an admin wants to see and fix drift now
instead of waiting (заявка 27.08.2026).

`/role_audit` used to live here too — a read-only report of rank/referral
role holders with no bound player. It was removed on the owner's request
(заявка 13.09.2026 п.11): `/resync_roles` already reports every role it
actually changed, and the "held a role but isn't in the database" case it
looked for is now handled at the source instead, by `/unlink_discord`
stripping the roles as part of unbinding (`cogs/manual.py`).
"""

from collections.abc import Sequence
from typing import Final

import discord
from discord import app_commands
from discord.ext import commands

from stalbot.application.dto.role_change import RoleChange
from stalbot.application.services.progression import ProgressionService
from stalbot.config.ids import PARTNER_ROLE_ID
from stalbot.domain.progression.ranks import RankLadder
from stalbot.domain.progression.referrals import ReferralLadder
from stalbot.presentation.checks import admin_only
from stalbot.presentation.embeds.factory import EmbedFactory, enforce_limits
from stalbot.presentation.views.paginated_embed import PaginatedEmbedView

_PAGE_SIZE: Final = 20


class RolesCog(commands.Cog):
    """`/resync_roles` — fixes rank/referral role drift on demand."""

    def __init__(
        self,
        embeds: EmbedFactory,
        progression: ProgressionService,
        *,
        rank_ladder: RankLadder | None = None,
        referral_ladder: ReferralLadder | None = None,
    ) -> None:
        """Wire the cog to its collaborators.

        Args:
            embeds: Builds every embed this cog sends.
            progression: Recomputes and reconciles every player's roles
                against the current ladder state.
            rank_ladder: Defaults to a fresh `RankLadder()`.
            referral_ladder: Defaults to a fresh `ReferralLadder()`.
        """
        self._embeds = embeds
        self._progression = progression
        self._rank_ladder = rank_ladder or RankLadder()
        self._referral_ladder = referral_ladder or ReferralLadder()

    @app_commands.command(
        name="resync_roles",
        description="🛡️ [Админ] 🔄 Пересчитать и исправить роли всех игроков сейчас",
    )
    @admin_only()
    async def resync_roles(self, interaction: discord.Interaction) -> None:
        """Handle `/resync_roles`: force `ProgressionService.resync_all()` and report changes."""
        await interaction.response.defer(ephemeral=True)
        channel = interaction.channel
        announce_to = channel if isinstance(channel, discord.abc.Messageable) else None
        changes = await self._progression.resync_all(announce_to=announce_to)

        pages = self._build_resync_pages(changes)
        if len(pages) == 1:
            await interaction.followup.send(embed=pages[0], ephemeral=True)
            return
        pager = PaginatedEmbedView(pages=pages, author_id=interaction.user.id)
        message = await interaction.followup.send(
            embed=pager.current, view=pager, ephemeral=True, wait=True
        )
        pager.message = message

    def _build_resync_pages(self, changes: Sequence[RoleChange]) -> list[discord.Embed]:
        if not changes:
            return [self._embeds.success("🔄 Синхронизация ролей", "Роли уже были в порядке.")]

        chunks = _chunk(changes, _PAGE_SIZE)
        pages: list[discord.Embed] = []
        for index, chunk in enumerate(chunks, start=1):
            title = (
                "🔄 Синхронизация ролей"
                if len(chunks) == 1
                else f"🔄 Синхронизация ролей (стр. {index}/{len(chunks)})"
            )
            summary = f"Изменено ролей у игроков: {len(changes)}." if index == 1 else None
            embed = self._embeds.success(title, summary)
            for change in chunk:
                lines = []
                if change.granted:
                    labels = ", ".join(self._role_label(role_id) for role_id in change.granted)
                    lines.append(f"➕ Выдано: {labels}")
                if change.revoked:
                    labels = ", ".join(self._role_label(role_id) for role_id in change.revoked)
                    lines.append(f"➖ Снято: {labels}")
                embed.add_field(
                    name=f"{change.nick} (<@{change.discord_id}>)",
                    value="\n".join(lines),
                    inline=False,
                )
            pages.append(enforce_limits(embed))
        return pages

    def _role_label(self, role_id: int) -> str:
        rank = self._rank_ladder.by_role_id(role_id)
        if rank is not None:
            return rank.label
        referral = self._referral_ladder.by_role_id(role_id)
        if referral is not None:
            return referral.label
        return "🎩 Партнёр" if role_id == PARTNER_ROLE_ID else str(role_id)


def _chunk[T](items: Sequence[T], size: int) -> list[Sequence[T]]:
    return [items[i : i + size] for i in range(0, len(items), size)]
