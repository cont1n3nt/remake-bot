"""Referral-role ladder: `REFERRAL_ROLES` and `ReferralLadder` (PLAN.md §9.1, §9.1.1).

Thresholds are the canonical, formula-verified values (decision A6) — they
match the live `S` column formula exactly (`test_ladder_matches_sheet_formula`
pins this down), not the numbers from the original text description.
"""

from dataclasses import dataclass
from typing import Final

from stalbot.config.ids import PARTNER_ROLE_ID, REFERRAL_ROLE_IDS
from stalbot.domain.progression.ladder import Ladder
from stalbot.domain.progression.perks import referral_perks

__all__ = ["PARTNER_ROLE_ID", "REFERRAL_ROLES", "ReferralLadder", "ReferralTier"]


@dataclass(frozen=True, slots=True)
class ReferralTier:
    """One rung of the referral-role ladder."""

    key: str
    label: str
    role_id: int
    referrals_required: int
    perks: tuple[str, ...]


REFERRAL_ROLES: Final[tuple[ReferralTier, ...]] = (
    ReferralTier("scout", "🧭 Скаут", REFERRAL_ROLE_IDS["scout"], 1, referral_perks("scout")),
    ReferralTier(
        "promoter", "📣 Промоутер", REFERRAL_ROLE_IDS["promoter"], 3, referral_perks("promoter")
    ),
    ReferralTier(
        "recruiter", "🧲 Вербовщик", REFERRAL_ROLE_IDS["recruiter"], 7, referral_perks("recruiter")
    ),
    ReferralTier(
        "ambassador",
        "📢 Амбассадор",
        REFERRAL_ROLE_IDS["ambassador"],
        20,
        referral_perks("ambassador"),
    ),
    ReferralTier(
        "baron", "🎩 Рекламный Барон", REFERRAL_ROLE_IDS["baron"], 50, referral_perks("baron")
    ),
)


class ReferralLadder(Ladder[ReferralTier]):
    """The five referral-role tiers, ordered by referral-count threshold."""

    def __init__(self) -> None:
        """Build the ladder from the canonical `REFERRAL_ROLES` tuple."""
        super().__init__(REFERRAL_ROLES, threshold_of=lambda tier: tier.referrals_required)
