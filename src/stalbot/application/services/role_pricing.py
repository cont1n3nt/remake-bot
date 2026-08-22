"""Resolve a ticket author's rank into the price multiplier for their role (§9.1).

The заказчик's role-based markup/discount (заявка 21.08.2026, п.2) hooks
into the existing rank ladder rather than a separate role system — the
"role" is whichever `RankTier` the member currently holds.
"""

from decimal import Decimal

from stalbot.config.role_pricing import ROLE_PRICE_MULTIPLIERS
from stalbot.domain.progression.ranks import RankLadder, RankTier

_NO_DISCOUNT: Decimal = Decimal("1.00")


def resolve_price_multiplier(
    role_ids: frozenset[int], ladder: RankLadder
) -> tuple[RankTier | None, Decimal]:
    """Return the member's highest-held rank tier and its price multiplier.

    Args:
        role_ids: Every Discord role id the ticket author currently holds.
        ladder: The rank ladder (`RankLadder()` — the ladder tiers are
            static, only levels/thresholds vary, and this doesn't).

    Returns:
        `(tier, multiplier)` — `(None, 1.00)` if the member holds none of
        the ladder's rank roles.
    """
    for tier in reversed(ladder.tiers):
        if tier.role_id in role_ids:
            return tier, ROLE_PRICE_MULTIPLIERS.get(tier.key, _NO_DISCOUNT)
    return None, _NO_DISCOUNT
