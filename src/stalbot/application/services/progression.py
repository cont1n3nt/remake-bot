"""Rank/referral-role progression: role sync + promotion announcements (PLAN.md §9.2).

`ProgressionService.sync()` never computes a rank or referral role itself —
it only reads what the sheet's own formulas already decided (`UserProfile.
rank`/`referral_role`, decision A2) and reconciles Discord roles + announces
genuine advancements against that.
"""

import logging
from collections.abc import Collection
from dataclasses import replace

import discord

from stalbot.application.dto.audit_event import AuditEvent
from stalbot.application.dto.progression_state import ProgressionState
from stalbot.application.dto.promotion import Promotion, PromotionAxis
from stalbot.application.ports.audit_gateway import AuditGateway
from stalbot.application.ports.clock import Clock
from stalbot.application.ports.role_gateway import RoleGateway, RoleSet
from stalbot.application.services.audit import AuditService
from stalbot.domain.entities.user_profile import UserProfile
from stalbot.domain.nick import NormalizedNick
from stalbot.domain.progression.ladder import Ladder, Tier
from stalbot.domain.progression.ranks import RankLadder
from stalbot.domain.progression.referrals import ReferralLadder
from stalbot.infrastructure.cache.repositories.progression_state import ProgressionStateRepository
from stalbot.infrastructure.cache.repositories.users import UsersCacheRepository
from stalbot.infrastructure.logging.trace import current_trace_id
from stalbot.infrastructure.sheets.a1 import a1_range
from stalbot.infrastructure.sheets.client import SheetsClient
from stalbot.infrastructure.sheets.layouts import DATABASE_SHEET
from stalbot.presentation.embeds.factory import EmbedFactory

logger = logging.getLogger(__name__)

#: The one raw column `ProgressionService` ever writes — the booster flag
#: (PLAN.md §9.2: `on_member_update` -> column `Q`). `Q` is not
#: formula-owned (PLAN.md §6.1), so this is a plain, protected write.
_BOOSTER_COLUMN = "Q"


class ProgressionService:
    """Syncs Discord roles to `rank`/`referral_role` and announces promotions."""

    def __init__(
        self,
        users: UsersCacheRepository,
        progression_state: ProgressionStateRepository,
        roles: RoleGateway,
        audit_gateway: AuditGateway,
        audit_service: AuditService,
        embeds: EmbedFactory,
        *,
        sheets: SheetsClient,
        clock: Clock,
        rank_ladder: RankLadder | None = None,
        referral_ladder: ReferralLadder | None = None,
    ) -> None:
        """Wire the service to its collaborators.

        Args:
            users: Cache repository for player profiles.
            progression_state: Tracks the last-announced rank/referral role.
            roles: Grants/revokes Discord roles.
            audit_gateway: Fallback destination for celebrations with no
                event channel (the background poller) — the log channel.
            audit_service: Records the audit-trail entry for each promotion.
            embeds: Builds the celebration embed.
            sheets: Writes the booster flag (`on_member_update` -> column `Q`).
            clock: Time source, tz-aware `GMT3`.
            rank_ladder: Defaults to a fresh `RankLadder()`.
            referral_ladder: Defaults to a fresh `ReferralLadder()`.
        """
        self._users = users
        self._progression_state = progression_state
        self._roles = roles
        self._audit_gateway = audit_gateway
        self._audit_service = audit_service
        self._embeds = embeds
        self._sheets = sheets
        self._clock = clock
        self._rank_ladder = rank_ladder or RankLadder()
        self._referral_ladder = referral_ladder or ReferralLadder()
        self._role_universe = self._rank_ladder.role_ids | self._referral_ladder.role_ids

    async def sync(
        self,
        nicks: Collection[NormalizedNick] | None = None,
        *,
        announce_to: discord.abc.Messageable | None = None,
    ) -> list[Promotion]:
        """Reconcile roles for the given players (or everyone) and announce promotions.

        Args:
            nicks: Players to sync, or `None` to sync the entire cached
                user base (the background poller's mode).
            announce_to: Where a public celebration is posted. `None` means
                there is no event channel (background poller) — celebrations
                fall back to the log channel.

        Returns:
            Every promotion actually detected and announced, across all
            synced players.
        """
        profiles = await self._load_profiles(nicks)
        promotions: list[Promotion] = []
        for profile in profiles:
            promotions.extend(await self._sync_one(profile, announce_to=announce_to))
        return promotions

    async def _load_profiles(self, nicks: Collection[NormalizedNick] | None) -> list[UserProfile]:
        if nicks is None:
            return list(await self._users.all())
        profiles: list[UserProfile] = []
        for nick in nicks:
            profile = await self._users.get_by_nick(nick)
            if profile is not None:
                profiles.append(profile)
        return profiles

    async def _sync_one(
        self, profile: UserProfile, *, announce_to: discord.abc.Messageable | None
    ) -> list[Promotion]:
        if profile.discord_id is None:
            return []  # nothing to grant a role to
        discord_id = profile.discord_id

        previous = await self._progression_state.get(profile.nick)
        # PLAN.md §10.12: while a rank was assigned manually via /set_rank
        # (M8), the poller leaves the rank ladder alone entirely — it is
        # excluded from both the desired set and the revocation universe.
        manual_rank = previous is not None and previous.manual_rank_role

        rank_tier = None
        if not manual_rank and profile.rank:
            rank_tier = self._rank_ladder.by_label(profile.rank)
        referral_tier = (
            self._referral_ladder.by_label(profile.referral_role) if profile.referral_role else None
        )
        desired = frozenset(tier.role_id for tier in (rank_tier, referral_tier) if tier is not None)
        universe = self._referral_ladder.role_ids if manual_rank else self._role_universe
        await self._roles.sync_roles(discord_id, RoleSet(desired=desired, universe=universe))

        promotions: list[Promotion] = []
        if previous is not None:
            if rank_tier is not None and profile.rank != previous.last_rank:
                if _is_advancement(self._rank_ladder, previous.last_rank, rank_tier):
                    promotions.append(_promotion(profile, discord_id, "rank", rank_tier))
            if (
                referral_tier is not None
                and profile.referral_role != previous.last_referral_role
                and _is_advancement(
                    self._referral_ladder, previous.last_referral_role, referral_tier
                )
            ):
                promotions.append(_promotion(profile, discord_id, "referral_role", referral_tier))

        await self._progression_state.upsert(
            ProgressionState(
                nick=profile.nick,
                last_rank=profile.rank,
                last_referral_role=profile.referral_role,
                manual_rank_role=manual_rank,
                announced_at=(
                    self._clock.now()
                    if promotions
                    else (previous.announced_at if previous is not None else None)
                ),
            )
        )

        for promotion in promotions:
            await self._announce(promotion, announce_to)

        return promotions

    async def sync_booster_flag(self, discord_id: int, is_boosting: bool) -> None:
        """Record a server-boost transition and resync that player's progression.

        Writes column `Q` (PLAN.md §6.1: raw data the bot owns, not a
        formula), refreshes the cache, then runs a normal `sync()` for that
        one player — a boost changes the `K`/`L` formula's bonus, which can
        itself trigger a rank promotion.

        Args:
            discord_id: The Discord member whose boost status changed.
            is_boosting: Whether they are boosting the server now.
        """
        profile = await self._users.get_by_discord_id(discord_id)
        if profile is None or profile.is_booster == is_boosting:
            return  # not bound to a nick yet, or already correct — nothing to do

        ref = a1_range(DATABASE_SHEET, _BOOSTER_COLUMN, profile.row)
        await self._sheets.batch_update({ref: [[is_boosting]]})

        display = await self._users.get_nick_display(profile.nick) or profile.nick
        await self._users.upsert_many(
            [replace(profile, is_booster=is_boosting)],
            nick_displays={profile.nick: display},
            synced_at=self._clock.now().isoformat(),
        )

        await self.sync([profile.nick])

    async def _announce(
        self, promotion: Promotion, announce_to: discord.abc.Messageable | None
    ) -> None:
        lines = [
            f"<@{promotion.discord_id}> поднялся до {promotion.label}!",
            "",
            f"📊 Сейчас: 🪙 {promotion.coins} Coins • ⚡ {promotion.xp} XP",
            *promotion.perks,
        ]
        embed = self._embeds.success("🎉 Повышение!", "\n".join(lines))

        if announce_to is not None:
            await announce_to.send(embed=embed)
        else:
            await self._audit_gateway.send_batch([embed])

        self._audit_service.record(
            AuditEvent(
                user_id=promotion.discord_id,
                user_display=str(promotion.nick),
                channel_display=_channel_display(announce_to),
                command="progression.sync",
                arguments=f"ник={promotion.nick} • {promotion.axis}={promotion.label}",
                result="Повышение",
                duration_seconds=0.0,
                trace_id=current_trace_id(),
                occurred_at=self._clock.now(),
            )
        )


def _is_advancement[T: Tier](ladder: Ladder[T], previous_label: str | None, new_tier: T) -> bool:
    """Whether moving to `new_tier` is a genuine step up, not a drop or lateral move."""
    if previous_label is None:
        return True
    previous_tier = ladder.by_label(previous_label)
    if previous_tier is None:
        return True
    return ladder.tiers.index(new_tier) > ladder.tiers.index(previous_tier)


def _promotion[T: Tier](
    profile: UserProfile, discord_id: int, axis: PromotionAxis, tier: T
) -> Promotion:
    return Promotion(
        nick=profile.nick,
        discord_id=discord_id,
        axis=axis,
        label=tier.label,
        perks=tier.perks,
        coins=profile.coins,
        xp=profile.xp,
    )


def _channel_display(destination: discord.abc.Messageable | None) -> str:
    name = getattr(destination, "name", None)
    return f"#{name}" if name else "лог-канал"
