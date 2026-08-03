"""`/profile`, `/referrals` — player self-service lookups (PLAN.md §10.2, §10.3).

Unlike every other command in the project, these two are not `@admin_only()`
— any player can run them, but `ProfileService` restricts the result to the
requester's own bound nick unless they are an admin and
`ADMIN_CAN_VIEW_ANY_PROFILE` allows otherwise (PLAN.md §5.5, §10.2).
"""

from collections.abc import Sequence

import discord
from discord import app_commands
from discord.ext import commands

from stalbot.application.dto.profile_view import ProfileView, ReferredPlayer
from stalbot.application.services.profile import ProfileService
from stalbot.config.settings import Settings
from stalbot.domain.money import format_amount
from stalbot.domain.progression.ladder import Ladder, Tier
from stalbot.domain.progression.ranks import RankLadder
from stalbot.domain.progression.referrals import ReferralLadder
from stalbot.presentation.embeds.factory import EmbedFactory
from stalbot.presentation.embeds.progress import render_progress_bar
from stalbot.presentation.views.paginated_embed import PaginatedEmbedView

_SEPARATOR = "━━━━━━━━━━━━━━━━━━━━━"
_REFERRALS_PAGE_SIZE = 15


def _is_admin(user: discord.User | discord.Member) -> bool:
    return isinstance(user, discord.Member) and user.guild_permissions.administrator


class ProfileCog(commands.Cog):
    """`/profile` and `/referrals` — read-only progression lookups."""

    def __init__(
        self,
        profile: ProfileService,
        embeds: EmbedFactory,
        settings: Settings,
        *,
        rank_ladder: RankLadder | None = None,
        referral_ladder: ReferralLadder | None = None,
    ) -> None:
        """Wire the cog to the service it delegates to.

        Args:
            profile: Enforces the binding check and looks up profiles.
            embeds: Builds every embed this cog sends.
            settings: For `ADMIN_CAN_VIEW_ANY_PROFILE`.
            rank_ladder: Defaults to a fresh `RankLadder()`.
            referral_ladder: Defaults to a fresh `ReferralLadder()`.
        """
        self._profile = profile
        self._embeds = embeds
        self._settings = settings
        self._rank_ladder = rank_ladder or RankLadder()
        self._referral_ladder = referral_ladder or ReferralLadder()

    @app_commands.command(name="profile", description="👤 Посмотреть профиль игрока")
    @app_commands.describe(ник="Игровой ник")
    async def profile(self, interaction: discord.Interaction, ник: str) -> None:
        """Handle `/profile`: look up and show one player's progression state."""
        await interaction.response.defer(ephemeral=True)
        view = await self._profile.get_profile(
            ник,
            requester_id=interaction.user.id,
            is_admin=_is_admin(interaction.user),
            admin_can_view_any=self._settings.admin_can_view_any_profile,
        )
        embed = self._build_profile_embed(view)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="referrals", description="🤝 Посмотреть рефералов игрока")
    @app_commands.describe(ник="Игровой ник")
    async def referrals(self, interaction: discord.Interaction, ник: str) -> None:
        """Handle `/referrals`: show a player's referral role and referred list."""
        await interaction.response.defer(ephemeral=True)
        view, referred = await self._profile.list_referrals(
            ник,
            requester_id=interaction.user.id,
            is_admin=_is_admin(interaction.user),
            admin_can_view_any=self._settings.admin_can_view_any_profile,
        )
        pages = self._build_referrals_pages(view, referred)
        if len(pages) == 1:
            await interaction.followup.send(embed=pages[0], ephemeral=True)
            return

        pager = PaginatedEmbedView(pages=pages, author_id=interaction.user.id)
        message = await interaction.followup.send(
            embed=pager.current, view=pager, ephemeral=True, wait=True
        )
        pager.message = message

    def _build_profile_embed(self, view: ProfileView) -> discord.Embed:
        profile = view.profile
        rank_label = profile.rank or "—"
        referral_label = profile.referral_role or "—"

        lines = [
            f"🪙 Coins {format_amount(profile.coins, currency=False)}"
            f"    ⚡ XP {format_amount(profile.xp, currency=False)}"
            f"    🏅 Ранг {rank_label}",
            f"🤝 Реф-роль {referral_label}    👥 Приглашено {profile.referrals_count}",
            _SEPARATOR,
        ]

        rank_tier = self._rank_ladder.by_label(profile.rank) if profile.rank else None
        if rank_tier is not None:
            lines.append(f"🎁 Бонусы ранга {rank_tier.label}")
            lines.extend(f" • {perk}" for perk in rank_tier.perks)
            lines.append(_SEPARATOR)

        lines.extend(_progress_lines(self._rank_ladder, profile.xp, unit="XP"))

        embed = self._embeds.info(f"👤 Профиль — {view.nick_display}", "\n".join(lines))
        return embed

    def _build_referrals_pages(
        self, view: ProfileView, referred: Sequence[ReferredPlayer]
    ) -> list[discord.Embed]:
        profile = view.profile
        referral_label = profile.referral_role or "—"

        header = [f"🤝 Реф-роль {referral_label}"]
        referral_tier = (
            self._referral_ladder.by_label(profile.referral_role) if profile.referral_role else None
        )
        if referral_tier is not None:
            header.extend(f" • {perk}" for perk in referral_tier.perks)
        header.append(_SEPARATOR)

        referral_progress = _progress_lines(
            self._referral_ladder, profile.referrals_count, unit="реф."
        )
        footer = [_SEPARATOR, *referral_progress]
        next_tier = self._referral_ladder.next(profile.referrals_count)
        if next_tier is not None:
            footer.append(f"🎁 Награда: {'; '.join(next_tier.perks)}")

        entries = [
            f" • {entry.nick_display} → " + (f"<@{entry.discord_id}>" if entry.discord_id else "—")
            for entry in referred
        ]
        chunks = _chunk(entries, _REFERRALS_PAGE_SIZE) or [[]]

        title = f"🤝 Рефералы — {view.nick_display}"
        pages: list[discord.Embed] = []
        for index, chunk in enumerate(chunks, start=1):
            list_lines = chunk if chunk else ["Пока никого не пригласил(а)."]
            body = [*header, f"👥 Приглашено ({len(referred)})", *list_lines, *footer]
            page_title = title if len(chunks) == 1 else f"{title} (стр. {index}/{len(chunks)})"
            pages.append(self._embeds.info(page_title, "\n".join(body)))
        return pages


def _progress_lines[T: Tier](ladder: Ladder[T], value: int, *, unit: str) -> list[str]:
    next_tier = ladder.next(value)
    if next_tier is None:
        return ["🏆 Достигнут максимальный уровень"]
    progress = ladder.progress(value)
    return [
        f"📈 До {next_tier.label}",
        f"{render_progress_bar(progress.done, progress.need)}"
        f"  {value} / {ladder.threshold_of(next_tier)} {unit}",
    ]


def _chunk[T](items: Sequence[T], size: int) -> list[list[T]]:
    return [list(items[i : i + size]) for i in range(0, len(items), size)]
