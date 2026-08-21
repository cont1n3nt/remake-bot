"""Result DTOs for `ManualGrantService` (PLAN.md §10.12; sqlite_migration.md Э7)."""

from dataclasses import dataclass

from stalbot.domain.nick import NormalizedNick
from stalbot.domain.progression.ranks import RankTier


@dataclass(frozen=True, slots=True)
class SetReferralResult:
    """What `/set_referral` actually did, for the caller to build a response from."""

    previous_referrer: NormalizedNick | None
    """Whatever referrer was on record before this call, if any."""
    player_discord_bound: bool
    referrer_discord_bound: bool


@dataclass(frozen=True, slots=True)
class SetRankResult:
    """What `/set_rank` actually did, for the caller to build a response from."""

    tier: RankTier
    granted: bool
    """`True` if the role was granted, `False` if a repeat call toggled it off."""
