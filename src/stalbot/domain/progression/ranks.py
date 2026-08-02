"""Rank ladder: `RANKS` and `RankLadder` (see PLAN.md §9.1, §9.1.1).

Thresholds are the canonical, formula-verified values (decision A6) — they
match the live `R` column formula exactly (`test_ladder_matches_sheet_formula`
pins this down), not the numbers from the original text description.
"""

from dataclasses import dataclass
from typing import Final

from stalbot.config.ids import RANK_ROLE_IDS
from stalbot.domain.progression.ladder import Ladder
from stalbot.domain.progression.perks import rank_perks


@dataclass(frozen=True, slots=True)
class RankTier:
    """One rung of the rank ladder."""

    key: str
    label: str
    role_id: int
    xp_required: int
    perks: tuple[str, ...]


RANKS: Final[tuple[RankTier, ...]] = (
    RankTier("standard", "🔹 Standard", RANK_ROLE_IDS["standard"], 50, rank_perks("standard")),
    RankTier("premium", "🔷 Premium", RANK_ROLE_IDS["premium"], 300, rank_perks("premium")),
    RankTier("prestige", "💠 Prestige", RANK_ROLE_IDS["prestige"], 1200, rank_perks("prestige")),
    RankTier("elite", "💎 Elite", RANK_ROLE_IDS["elite"], 3500, rank_perks("elite")),
    RankTier("legend", "👑 Legend", RANK_ROLE_IDS["legend"], 7000, rank_perks("legend")),
)


class RankLadder(Ladder[RankTier]):
    """The five rank tiers, ordered by XP threshold."""

    def __init__(self) -> None:
        """Build the ladder from the canonical `RANKS` tuple."""
        super().__init__(RANKS, threshold_of=lambda tier: tier.xp_required)
