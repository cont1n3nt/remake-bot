"""`/set_referral`, `/set_rank` — hand-operated overrides outside the deal flow.

(PLAN.md §10.12; sqlite_migration.md Э7.)

Both bypass the mechanisms that normally decide these values: `/set_referral`
sets `players.referrer_player_id` directly instead of that happening as a
side effect of `TransactionService.register()`'s first-deal check, and
`/set_rank` grants a Discord role without touching the materialized
progression at all — a "manual rank" is a role that intentionally does not
match the calculator's own answer.

`/set_referral` no longer requires the player to have an existing deal:
`players` is a standalone table (sqlite_migration.md §XIV.1, §III.3) — a
player row (and so a referrer) can exist before any deal does, unlike the
sheet-era "referrer lives on the first `Тикеты` row" constraint this service
used to enforce.
"""

from stalbot.application.dto.manual_grant import SetRankResult, SetReferralResult
from stalbot.application.dto.progression_state import ProgressionState
from stalbot.application.ports.clock import Clock
from stalbot.application.ports.role_gateway import RoleGateway, RoleSet
from stalbot.application.services.binding import bind_discord
from stalbot.domain.nick import NormalizedNick, normalize_nick
from stalbot.domain.progression.ranks import RankLadder, RankTier
from stalbot.infrastructure.cache.repositories.players import PlayersRepository
from stalbot.infrastructure.cache.repositories.progression import ProgressionRepository
from stalbot.infrastructure.cache.repositories.progression_state import ProgressionStateRepository


class ManualGrantService:
    """Backs `/set_referral` and `/set_rank`."""

    def __init__(
        self,
        players: PlayersRepository,
        progression: ProgressionRepository,
        progression_state: ProgressionStateRepository,
        roles: RoleGateway,
        *,
        clock: Clock,
        rank_ladder: RankLadder | None = None,
    ) -> None:
        """Wire the service to its collaborators.

        Args:
            players: Cache repository for player identity/binding/referrer.
            progression: Recomputes Coins/XP/rank after the referrer changes.
            progression_state: Tracks the `manual_rank_role` flag.
            roles: Grants/revokes the manually-assigned rank role.
            clock: Time source, tz-aware `GMT3`.
            rank_ladder: Defaults to a fresh `RankLadder()`.
        """
        self._players = players
        self._progression = progression
        self._progression_state = progression_state
        self._roles = roles
        self._clock = clock
        self._rank_ladder = rank_ladder or RankLadder()

    async def current_referrer(self, nick: str) -> NormalizedNick | None:
        """Return the referrer already on record for `nick`, if any.

        Args:
            nick: Game nick, any casing.
        """
        player = await self._players.get_by_nick(normalize_nick(nick))
        if player is None or player.referrer_player_id is None:
            return None
        referrer = await self._players.get_by_id(player.referrer_player_id)
        return referrer.nick_norm if referrer is not None else None

    async def set_referral(
        self,
        nick: str,
        referrer_nick: str,
        discord_id: int,
        referrer_discord_id: int,
    ) -> SetReferralResult:
        """Set `nick`'s `referrer_player_id` to `referrer_nick` (PLAN.md §10.12).

        Creates either player row if it doesn't exist yet — a referral can
        be set up front, before either side has ever recorded a deal.

        Args:
            nick: The referred player's game nick.
            referrer_nick: The referrer's game nick.
            discord_id: Discord id to bind to `nick`.
            referrer_discord_id: Discord id to bind to `referrer_nick`.

        Returns:
            What was written and which bindings changed.
        """
        nick_norm = normalize_nick(nick)
        referrer_norm = normalize_nick(referrer_nick)
        now = self._clock.now()

        player = await self._players.get_or_create(nick_norm, nick, now=now)
        referrer = await self._players.get_or_create(referrer_norm, referrer_nick, now=now)
        assert player.id is not None  # noqa: S101 - get_or_create always returns a persisted player
        assert referrer.id is not None  # noqa: S101 - get_or_create always returns a persisted player

        previous_referrer_nick: NormalizedNick | None = None
        if player.referrer_player_id is not None:
            previous = await self._players.get_by_id(player.referrer_player_id)
            previous_referrer_nick = previous.nick_norm if previous is not None else None

        await self._players.set_referrer(player.id, referrer.id, now=now)
        await self._progression.recompute([player.id, referrer.id], now=now)

        player_bound = await bind_discord(
            self._players, self._clock, nick_norm, discord_id, force=False
        )
        referrer_bound = await bind_discord(
            self._players, self._clock, referrer_norm, referrer_discord_id, force=False
        )

        return SetReferralResult(
            previous_referrer=previous_referrer_nick,
            player_discord_bound=player_bound,
            referrer_discord_bound=referrer_bound,
        )

    async def set_rank(
        self, nick: str, discord_id: int, tier: RankTier, *, revoke: bool
    ) -> SetRankResult:
        """Grant or revoke a manually-assigned rank role. Materialized progression is never touched.

        Args:
            nick: Game nick the role is tracked against (`progression_state` key).
            discord_id: Discord member to grant/revoke the role for.
            tier: The rank tier to grant (or, on `revoke`, take away).
            revoke: `True` toggles the role off instead of granting it — a
                repeat `/set_rank` call with the same rank (PLAN.md §10.12).

        Returns:
            The tier acted on and whether it ended up granted or revoked.
        """
        nick_norm = normalize_nick(nick)
        desired = frozenset() if revoke else frozenset({tier.role_id})
        await self._roles.sync_roles(
            discord_id, RoleSet(desired=desired, universe=self._rank_ladder.role_ids)
        )

        previous = await self._progression_state.get(nick_norm)
        await self._progression_state.upsert(
            ProgressionState(
                nick=nick_norm,
                last_rank=previous.last_rank if previous else None,
                last_referral_role=previous.last_referral_role if previous else None,
                manual_rank_role=not revoke,
                announced_at=previous.announced_at if previous else None,
            )
        )
        return SetRankResult(tier=tier, granted=not revoke)
