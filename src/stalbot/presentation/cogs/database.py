"""`/database` — full player directory, paginated (заявка 21.08.2026 п.3).

Every `players` row plus its materialized progression, one page at a time.
Read-only: `PlayersRepository.all()` + `ProgressionRepository.all()`, no
new state of its own.
"""

from collections.abc import Sequence
from typing import Final

import discord
from discord import app_commands
from discord.ext import commands

from stalbot.domain.clock import format_datetime
from stalbot.domain.entities.player import Player
from stalbot.domain.entities.player_progression import PlayerProgressionRecord
from stalbot.domain.money import format_amount
from stalbot.domain.progression.ranks import RankLadder
from stalbot.domain.progression.referrals import ReferralLadder
from stalbot.infrastructure.cache.repositories.players import PlayersRepository
from stalbot.infrastructure.cache.repositories.progression import ProgressionRepository
from stalbot.presentation.checks import admin_only
from stalbot.presentation.embeds.factory import EmbedFactory, enforce_limits
from stalbot.presentation.views.paginated_embed import PaginatedEmbedView

_PAGE_SIZE: Final = 8


class DatabaseCog(commands.Cog):
    """`/database` — the full player roster, browsable page by page."""

    def __init__(
        self,
        players: PlayersRepository,
        progression: ProgressionRepository,
        embeds: EmbedFactory,
        *,
        rank_ladder: RankLadder | None = None,
        referral_ladder: ReferralLadder | None = None,
    ) -> None:
        """Wire the cog to its read-only collaborators.

        Args:
            players: Source of every `players` row.
            progression: Source of each player's materialized progression.
            embeds: Builds every embed this cog sends.
            rank_ladder: Defaults to a fresh `RankLadder()`.
            referral_ladder: Defaults to a fresh `ReferralLadder()`.
        """
        self._players = players
        self._progression = progression
        self._embeds = embeds
        self._rank_ladder = rank_ladder or RankLadder()
        self._referral_ladder = referral_ladder or ReferralLadder()

    @app_commands.command(name="database", description="🛡️ [Админ] 🗄️ Полная база игроков")
    @admin_only()
    async def database(self, interaction: discord.Interaction) -> None:
        """Handle `/database`: page through every player with every field."""
        await interaction.response.defer(ephemeral=True)
        players = await self._players.all()
        progressions = {record.player_id: record for record in await self._progression.all()}

        pages = self._build_pages(players, progressions)
        if len(pages) == 1:
            await interaction.followup.send(embed=pages[0], ephemeral=True)
            return
        pager = PaginatedEmbedView(pages=pages, author_id=interaction.user.id)
        message = await interaction.followup.send(
            embed=pager.current, view=pager, ephemeral=True, wait=True
        )
        pager.message = message

    def _build_pages(
        self, players: Sequence[Player], progressions: dict[int, PlayerProgressionRecord]
    ) -> list[discord.Embed]:
        chunks = _chunk(players, _PAGE_SIZE) or [()]
        pages: list[discord.Embed] = []
        for index, chunk in enumerate(chunks, start=1):
            title = (
                "🗄️ База игроков"
                if len(chunks) == 1
                else f"🗄️ База игроков (стр. {index}/{len(chunks)})"
            )
            if not chunk:
                pages.append(self._embeds.info(title, "Пока нет игроков."))
                continue
            embed = self._embeds.info(title)
            for player in chunk:
                assert player.id is not None  # noqa: S101 - a fetched player always has an id
                embed.add_field(
                    name=player.nick_display,
                    value=self._format_player(player, progressions.get(player.id)),
                    inline=False,
                )
            pages.append(enforce_limits(embed))
        return pages

    def _format_player(
        self, player: Player, progression: PlayerProgressionRecord | None
    ) -> str:
        lines = [
            f"💬 Discord: {f'<@{player.discord_id}>' if player.discord_id else 'не привязан'}",
            f"🚀 Буст сервера: {'да' if player.is_booster else 'нет'}",
        ]
        if player.referrer_player_id is not None:
            lines.append(f"🤝 Реферер: ID {player.referrer_player_id}")
        if progression is not None:
            rank = self._rank_ladder.by_key(progression.rank_key) if progression.rank_key else None
            referral_role = (
                self._referral_ladder.by_key(progression.referral_role_key)
                if progression.referral_role_key
                else None
            )
            lines.append(
                f"🪙 {format_amount(progression.coins, currency=False)} Coins • "
                f"⚡ {format_amount(progression.xp, currency=False)} XP"
            )
            if rank is not None:
                lines.append(f"🏅 Ранг: {rank.label}")
            if referral_role is not None:
                lines.append(f"🤝 Реф-роль: {referral_role.label}")
            lines.append(f"👥 Приглашено: {progression.referral_count}")
            if progression.purchase_turnover:
                lines.append(f"📤 Оборот продаж: {format_amount(progression.purchase_turnover)}")
            if progression.sale_turnover:
                lines.append(f"📥 Оборот покупок: {format_amount(progression.sale_turnover)}")
            if progression.total_turnover:
                lines.append(f"💹 Общий оборот: {format_amount(progression.total_turnover)}")
        lines.append(f"🕒 Зарегистрирован: {format_datetime(player.created_at)}")
        return "\n".join(lines)


def _chunk[T](items: Sequence[T], size: int) -> list[Sequence[T]]:
    return [items[i : i + size] for i in range(0, len(items), size)]
