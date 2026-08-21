"""DTOs returned by `ProfileService` (PLAN.md §10.2, §10.3; sqlite_migration.md Э6)."""

from dataclasses import dataclass

from stalbot.domain.entities.player import Player
from stalbot.domain.entities.player_progression import PlayerProgressionRecord


@dataclass(frozen=True, slots=True)
class ProfileView:
    """A player plus their materialized progression, if any has been computed yet.

    `progression` is `None` for a player who exists (has a `players` row)
    but has never had `ProgressionRepository.recompute()` run for them —
    every derived field below defaults to zero/unset rather than the caller
    having to null-check `progression` at every use site.
    """

    player: Player
    progression: PlayerProgressionRecord | None
    nick_display: str

    @property
    def coins(self) -> int:
        """Materialized Coins, or `0` if `progression` is `None`."""
        return self.progression.coins if self.progression is not None else 0

    @property
    def xp(self) -> int:
        """Materialized XP, or `0` if `progression` is `None`."""
        return self.progression.xp if self.progression is not None else 0

    @property
    def referrals_count(self) -> int:
        """Number of players referred, or `0` if `progression` is `None`."""
        return self.progression.referral_count if self.progression is not None else 0

    @property
    def rank_key(self) -> str | None:
        """Current rank tier key, or `None` if unranked/`progression` is `None`."""
        return self.progression.rank_key if self.progression is not None else None

    @property
    def referral_role_key(self) -> str | None:
        """Current referral-role tier key, or `None` if none/`progression` is `None`."""
        return self.progression.referral_role_key if self.progression is not None else None


@dataclass(frozen=True, slots=True)
class ReferredPlayer:
    """One player a referrer brought in, as shown by `/referrals` (PLAN.md §10.3)."""

    nick_display: str
    discord_id: int | None
