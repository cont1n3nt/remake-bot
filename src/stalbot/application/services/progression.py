"""Rank/referral-role progression: role sync + promotion announcements (PLAN.md §9.2).

sqlite_migration.md Э6: `ProgressionService.sync()` reads the materialized
`player_progression` snapshot (`rank_key`/`referral_role_key` — stable ladder
keys, not the sheet's emoji-label text) instead of the sheet-era
`UserProfile.rank`/`.referral_role` strings, and looks tiers up via
`Ladder.by_key` instead of `Ladder.by_label`. `ProgressionRepository.recompute()`
(Э3's calculator) is what decides Coins/XP/rank now — this service only
reconciles Discord roles and announces genuine advancements against
whatever it already computed.
"""

import logging
from collections.abc import Collection

import discord

from stalbot.application.dto.audit_event import AuditEvent
from stalbot.application.dto.progression_state import ProgressionState
from stalbot.application.dto.promotion import Promotion, PromotionAxis
from stalbot.application.dto.role_change import RoleChange
from stalbot.application.ports.audit_gateway import AuditGateway
from stalbot.application.ports.clock import Clock
from stalbot.application.ports.role_gateway import RoleDiff, RoleGateway, RoleSet
from stalbot.application.services.audit import AuditService
from stalbot.domain.entities.player import Player
from stalbot.domain.entities.player_progression import PlayerProgressionRecord
from stalbot.domain.nick import NormalizedNick
from stalbot.domain.progression.ladder import Ladder, Tier
from stalbot.domain.progression.ranks import RankLadder
from stalbot.domain.progression.referrals import ReferralLadder
from stalbot.infrastructure.cache.repositories.players import PlayersRepository
from stalbot.infrastructure.cache.repositories.progression import ProgressionRepository
from stalbot.infrastructure.cache.repositories.progression_state import ProgressionStateRepository
from stalbot.infrastructure.logging.trace import current_trace_id
from stalbot.presentation.embeds.factory import EmbedFactory

logger = logging.getLogger(__name__)


class ProgressionService:
    """Syncs Discord roles to `rank_key`/`referral_role_key` and announces promotions."""

    def __init__(
        self,
        players: PlayersRepository,
        progression: ProgressionRepository,
        progression_state: ProgressionStateRepository,
        roles: RoleGateway,
        audit_gateway: AuditGateway,
        audit_service: AuditService,
        embeds: EmbedFactory,
        *,
        clock: Clock,
        rank_ladder: RankLadder | None = None,
        referral_ladder: ReferralLadder | None = None,
    ) -> None:
        """Wire the service to its collaborators.

        Args:
            players: Cache repository for player identity.
            progression: Cache repository for the materialized Coins/XP/rank
                snapshot each player's `rank_key`/`referral_role_key` come from.
            progression_state: Tracks the last-announced rank/referral role.
            roles: Grants/revokes Discord roles.
            audit_gateway: Fallback destination for celebrations with no
                event channel (the background poller) — the log channel.
            audit_service: Records the audit-trail entry for each promotion.
            embeds: Builds the celebration embed.
            clock: Time source, tz-aware `GMT3`.
            rank_ladder: Defaults to a fresh `RankLadder()`.
            referral_ladder: Defaults to a fresh `ReferralLadder()`.
        """
        self._players = players
        self._progression = progression
        self._progression_state = progression_state
        self._roles = roles
        self._audit_gateway = audit_gateway
        self._audit_service = audit_service
        self._embeds = embeds
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
            nicks: Players to sync, or `None` to sync the entire player base
                (the background poller's mode).
            announce_to: Where a public celebration is posted. `None` means
                there is no event channel (background poller) — celebrations
                fall back to the log channel.

        Returns:
            Every promotion actually detected and announced, across all
            synced players.
        """
        players = await self._load_players(nicks)
        promotions: list[Promotion] = []
        for player in players:
            player_promotions, _diff = await self._sync_one(player, announce_to=announce_to)
            promotions.extend(player_promotions)
        return promotions

    async def resync_all(
        self, *, announce_to: discord.abc.Messageable | None = None
    ) -> list[RoleChange]:
        """Force a right-now resync of every player and report every role actually changed.

        Unlike `sync()`, whose callers ignore what actually moved, this is
        for an admin-triggered "fix any drift now" command (заявка
        27.08.2026: "пересинхронизировать всех игроков... если у кого-то
        роли не соответствуют") — it surfaces exactly who was touched and
        which role ids were granted/revoked, instead of just promotions.

        Args:
            announce_to: Where a public celebration is posted for any
                promotion this resync also happens to trigger.

        Returns:
            One `RoleChange` per player who actually had a role granted or
            revoked (players already in sync, or with no linked Discord
            account, are omitted).
        """
        changes: list[RoleChange] = []
        for player in await self._load_players(None):
            if player.discord_id is None:
                continue
            _promotions, diff = await self._sync_one(player, announce_to=announce_to)
            if diff.granted or diff.revoked:
                changes.append(
                    RoleChange(
                        nick=player.nick_norm,
                        discord_id=player.discord_id,
                        granted=diff.granted,
                        revoked=diff.revoked,
                    )
                )
        return changes

    async def _load_players(self, nicks: Collection[NormalizedNick] | None) -> list[Player]:
        if nicks is None:
            return list(await self._players.all())
        players: list[Player] = []
        for nick in nicks:
            player = await self._players.get_by_nick(nick)
            if player is not None:
                players.append(player)
        return players

    async def _sync_one(
        self, player: Player, *, announce_to: discord.abc.Messageable | None
    ) -> tuple[list[Promotion], RoleDiff]:
        empty_diff = RoleDiff(granted=(), revoked=())
        if player.discord_id is None:
            return [], empty_diff  # nothing to grant a role to
        discord_id = player.discord_id
        assert player.id is not None  # noqa: S101 - a fetched player always has a persisted id

        record = await self._progression.get(player.id)
        previous = await self._progression_state.get(player.nick_norm)
        # PLAN.md §10.12: while a rank was assigned manually via /set_rank
        # (M8), the poller leaves the rank ladder alone entirely — it is
        # excluded from both the desired set and the revocation universe.
        manual_rank = previous is not None and previous.manual_rank_role

        rank_key = record.rank_key if record is not None else None
        referral_role_key = record.referral_role_key if record is not None else None

        rank_tier = None
        if not manual_rank and rank_key:
            rank_tier = self._rank_ladder.by_key(rank_key)
        referral_tier = (
            self._referral_ladder.by_key(referral_role_key) if referral_role_key else None
        )
        desired = frozenset(tier.role_id for tier in (rank_tier, referral_tier) if tier is not None)
        universe = self._referral_ladder.role_ids if manual_rank else self._role_universe
        diff = await self._roles.sync_roles(discord_id, RoleSet(desired=desired, universe=universe))

        promotions: list[Promotion] = []
        if previous is not None and record is not None:
            if rank_tier is not None and rank_key != previous.last_rank:
                if _is_advancement(self._rank_ladder, previous.last_rank, rank_tier):
                    promotions.append(_promotion(player, record, discord_id, "rank", rank_tier))
            if (
                referral_tier is not None
                and referral_role_key != previous.last_referral_role
                and _is_advancement(
                    self._referral_ladder, previous.last_referral_role, referral_tier
                )
            ):
                promotions.append(
                    _promotion(player, record, discord_id, "referral_role", referral_tier)
                )

        await self._progression_state.upsert(
            ProgressionState(
                nick=player.nick_norm,
                last_rank=rank_key,
                last_referral_role=referral_role_key,
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

        return promotions, diff

    async def sync_booster_flag(self, discord_id: int, is_boosting: bool) -> None:
        """Record a server-boost transition and resync that player's progression.

        Recomputes progression before resyncing that one player — a boost
        changes the calculator's booster bonus, which can itself trigger a
        rank promotion (sqlite_migration.md Э6: no more raw Sheets column
        write, `players.is_booster` plus a targeted `recompute()`).

        Args:
            discord_id: The Discord member whose boost status changed.
            is_boosting: Whether they are boosting the server now.
        """
        player = await self._players.get_by_discord_id(discord_id)
        if player is None or player.is_booster == is_boosting:
            return  # not bound to a nick yet, or already correct — nothing to do
        assert player.id is not None  # noqa: S101 - a fetched player always has a persisted id

        now = self._clock.now()
        await self._players.set_booster(player.id, is_boosting, now=now)
        await self._progression.recompute([player.id], now=now)

        await self.sync([player.nick_norm])

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


def _is_advancement[T: Tier](ladder: Ladder[T], previous_key: str | None, new_tier: T) -> bool:
    """Whether moving to `new_tier` is a genuine step up, not a drop or lateral move."""
    if previous_key is None:
        return True
    previous_tier = ladder.by_key(previous_key)
    if previous_tier is None:
        return True
    return ladder.tiers.index(new_tier) > ladder.tiers.index(previous_tier)


def _promotion[T: Tier](
    player: Player,
    record: PlayerProgressionRecord,
    discord_id: int,
    axis: PromotionAxis,
    tier: T,
) -> Promotion:
    return Promotion(
        nick=player.nick_norm,
        discord_id=discord_id,
        axis=axis,
        label=tier.label,
        perks=tier.perks,
        coins=record.coins,
        xp=record.xp,
    )


def _channel_display(destination: discord.abc.Messageable | None) -> str:
    name = getattr(destination, "name", None)
    return f"#{name}" if name else "лог-канал"
