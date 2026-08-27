"""`/role_audit` — rank/referral role holders with no linked player (заявка 21.08.2026 п.8).

One-time (or repeatable) reconciliation: every guild member holding a rank
or referral role is checked against `players.discord_id`. A role with
nothing bound to it means either the game nick was never linked (needs the
owner to supply it) or the role is stale and should be revoked — this
command only reports, it never touches roles itself (`ProgressionService.sync`
already owns granting/revoking on the live path, §9.2).
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
from stalbot.infrastructure.cache.repositories.players import PlayersRepository
from stalbot.presentation.checks import admin_only
from stalbot.presentation.embeds.factory import EmbedFactory, enforce_limits
from stalbot.presentation.views.paginated_embed import PaginatedEmbedView

_PAGE_SIZE: Final = 20


class RoleAuditCog(commands.Cog):
    """`/role_audit` (read-only) and `/resync_roles` (fixes drift now)."""

    def __init__(
        self,
        players: PlayersRepository,
        embeds: EmbedFactory,
        progression: ProgressionService,
        *,
        rank_ladder: RankLadder | None = None,
        referral_ladder: ReferralLadder | None = None,
    ) -> None:
        """Wire the cog to its collaborators.

        Args:
            players: Source of `discord_id` bindings to check role holders against.
            embeds: Builds every embed this cog sends.
            progression: Backs `/resync_roles` — recomputes and reconciles
                every player's roles against the current ladder state.
            rank_ladder: Defaults to a fresh `RankLadder()`.
            referral_ladder: Defaults to a fresh `ReferralLadder()`.
        """
        self._players = players
        self._embeds = embeds
        self._progression = progression
        self._rank_ladder = rank_ladder or RankLadder()
        self._referral_ladder = referral_ladder or ReferralLadder()

    @app_commands.command(
        name="role_audit", description="🛡️ [Админ] 🔍 Роли без привязанного игрока"
    )
    @admin_only()
    async def role_audit(self, interaction: discord.Interaction) -> None:
        """Handle `/role_audit`: list every ранг/реф-роль holder with no bound player."""
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if guild is None:
            embed = self._embeds.error("Ошибка", "Команда работает только на сервере.")
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        tracked_role_ids = self._tracked_role_ids()
        unmatched: list[tuple[discord.Member, list[int]]] = []
        checked = 0
        for member in guild.members:
            held = [role.id for role in member.roles if role.id in tracked_role_ids]
            if not held:
                continue
            checked += 1
            player = await self._players.get_by_discord_id(member.id)
            if player is None:
                unmatched.append((member, held))

        pages = self._build_pages(unmatched, checked)
        if len(pages) == 1:
            await interaction.followup.send(embed=pages[0], ephemeral=True)
            return
        pager = PaginatedEmbedView(pages=pages, author_id=interaction.user.id)
        message = await interaction.followup.send(
            embed=pager.current, view=pager, ephemeral=True, wait=True
        )
        pager.message = message

    @app_commands.command(
        name="resync_roles",
        description="🛡️ [Админ] 🔄 Пересчитать и исправить роли всех игроков сейчас",
    )
    @admin_only()
    async def resync_roles(self, interaction: discord.Interaction) -> None:
        """Handle `/resync_roles`: force `ProgressionService.resync_all()` and report changes.

        Same reconciliation the background poller already runs every 5
        minutes for every player, triggered on demand — for when an admin
        wants to see and fix drift right now instead of waiting.
        """
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

    def _tracked_role_ids(self) -> frozenset[int]:
        return self._rank_ladder.role_ids | self._referral_ladder.role_ids | {PARTNER_ROLE_ID}

    def _role_label(self, role_id: int) -> str:
        rank = self._rank_ladder.by_role_id(role_id)
        if rank is not None:
            return rank.label
        referral = self._referral_ladder.by_role_id(role_id)
        if referral is not None:
            return referral.label
        return "🎩 Партнёр" if role_id == PARTNER_ROLE_ID else str(role_id)

    def _build_pages(
        self, unmatched: Sequence[tuple[discord.Member, list[int]]], checked: int
    ) -> list[discord.Embed]:
        summary = (
            f"Проверено участников с ролью: {checked}. "
            f"Без привязанного игрока: {len(unmatched)}."
        )
        if not unmatched:
            return [self._embeds.success("🔍 Аудит ролей", f"{summary}\nВсё совпадает.")]

        chunks = _chunk(unmatched, _PAGE_SIZE)
        pages: list[discord.Embed] = []
        for index, chunk in enumerate(chunks, start=1):
            title = (
                "🔍 Аудит ролей"
                if len(chunks) == 1
                else f"🔍 Аудит ролей (стр. {index}/{len(chunks)})"
            )
            embed = self._embeds.warning(title, summary if index == 1 else None)
            for member, role_ids in chunk:
                labels = ", ".join(self._role_label(role_id) for role_id in role_ids)
                embed.add_field(
                    name=f"{member.display_name} (ID: {member.id})", value=labels, inline=False
                )
            pages.append(enforce_limits(embed))
        return pages


def _chunk[T](items: Sequence[T], size: int) -> list[Sequence[T]]:
    return [items[i : i + size] for i in range(0, len(items), size)]
