"""Pure progression calculator.

Reproduces the sheet's own K/L/M/N/O/P/R/S formulas exactly, including the
referral-turnover bug (sqlite_migration.md §V.1, §III.2).

No I/O, no `datetime.now()`, only `int` (money) and the two published
ladders. This module is deliberately table-faithful, not canon: the formula
in `compute_progression` below matches `DataBase!K3`/`L3` (frozen in
`tests/fixtures/sheet_snapshot_2026-08-10/formulas.json`) term for term,
bug included — the fixed referral resolution (variant B, §III.2) and the
canonical thresholds (Часть XIII) are a deliberate, reviewed *departure*
from this formula, applied upstream of `compute_progression` by whichever
code builds `PlayerAggregates` (the characterization test's
`aggregates_legacy()`/`aggregates_fixed()` today; `ProgressionRepository`'s
SQL from Э3 onward, per the owner's variant-B decision). `compute_progression`
itself has no opinion on how a referrer was resolved — it only turns
already-resolved aggregates into rewards, exactly as the sheet's formula
turns already-resolved `SUMIF`/`VLOOKUP` results into K/L.

Characterization test: `tests/unit/domain/progression/test_sheet_parity.py`
(level A) proves this module reproduces the frozen formula on all 244
players with zero tolerance.
"""

from dataclasses import dataclass
from typing import Final

from stalbot.domain.enums import DealType
from stalbot.domain.progression.perks import (
    BOOSTER_FLAT,
    PURCHASE_TURNOVER_PER_COIN,
    RANK_ONE_TIME_COINS,
    REFEREE_TURNOVER_TIERS,
    REFERRAL_ONE_TIME,
    REFERRED_TIERS,
    SALE_TURNOVER_PER_COIN,
    XP_BOOST_THRESHOLD,
    XP_PER_COIN_MILESTONE,
)
from stalbot.domain.progression.ranks import RankLadder, RankTier
from stalbot.domain.progression.referrals import ReferralLadder, ReferralTier

CALCULATOR_VERSION: Final = 1

#: `XP_BOOST_PERCENT` (5) applied as an exact-integer `ceil`, per
#: `ROUNDUP(...*1.05)` in the L3 formula — never float, per §III.3's
#: `Rub`/`Kopeks`/`CoinCenti` discipline against float drift.
_XP_BOOST_NUMERATOR: Final = 105
_XP_BOOST_DENOMINATOR: Final = 100

#: Ranks whose big-deal bonus is keyed on `deal_count_over_100m` (K3's
#: `(R3="Elite")+(R3="Legend")` term) vs `deal_count_over_50m` gated on
#: `R3="Prestige"` exactly (not "Prestige or above" — a literal equality in
#: the source formula, faithfully reproduced here).
_BIG_DEAL_100M_RANKS: Final = frozenset({"elite", "legend"})
_BIG_DEAL_50M_RANK: Final = "prestige"


@dataclass(frozen=True, slots=True)
class DealReward:
    """One deal's Coins/XP contribution — the sheet's `F`/`G` columns."""

    coins: int
    xp: int


def deal_reward(deal_type: DealType, amount: int) -> DealReward:
    """Compute a single deal's Coins/XP reward (`F3`/`G3` formulas).

    Args:
        deal_type: Which side of the deal (purchase/sale) — the two sides
            use different turnover-to-Coins rates.
        amount: The deal amount in whole rubles.
    """
    divisor = (
        PURCHASE_TURNOVER_PER_COIN if deal_type is DealType.PURCHASE else SALE_TURNOVER_PER_COIN
    )
    coins = amount // divisor
    return DealReward(coins=coins, xp=coins * XP_PER_COIN_MILESTONE)


@dataclass(frozen=True, slots=True)
class PlayerAggregates:
    """Everything `compute_progression` needs about one player, pre-aggregated.

    Building this struct — resolving who a player's referrer is, summing
    their deals — is explicitly out of scope here; see the module docstring.
    """

    purchase_turnover: int
    """M — sum of this player's own purchase deals."""
    sale_turnover: int
    """N — sum of this player's own sale deals."""
    coin_ledger_delta: int
    """Sum of `coin_ledger.delta` for this player (0 until Э12's shop exists)."""
    referral_count: int
    """P — number of players whose resolved referrer is this player."""
    referee_total_turnover: int
    """Combined M+N of every player this player referred (`rt` in §V.1)."""
    has_referrer: bool
    is_booster: bool
    booster_big_deal_count: int
    """Count of this player's own deals >= 10M (purchase) or >= 25M (sale)."""
    deal_count_over_50m: int
    """Count of this player's own deals >= 50,000,000 ₽ (any side)."""
    deal_count_over_100m: int
    """Count of this player's own deals >= 100,000,000 ₽ (any side)."""


@dataclass(frozen=True, slots=True)
class ProgressionBreakdown:
    """Every term that summed into `PlayerProgression.coins`/`.xp` — for audit/debug."""

    base_coins: int
    base_xp: int
    referee_turnover_coins: int
    referee_turnover_xp: int
    referred_coins: int
    referred_xp: int
    rank_one_time_coins: int
    referral_role_coins: int
    referral_role_xp: int
    booster_coins: int
    booster_xp: int
    big_deal_coins: int
    ledger_delta: int
    xp_boost_applied: bool


@dataclass(frozen=True, slots=True)
class PlayerProgression:
    """The sheet's `K`/`L`/`M`/`N`/`O`/`P`/`R`/`S` for one player, computed fresh."""

    coins: int
    """K."""
    xp: int
    """L."""
    purchase_turnover: int
    """M."""
    sale_turnover: int
    """N."""
    total_turnover: int
    """O = M + N."""
    referral_count: int
    """P (as given by `aggregates`, not recomputed)."""
    rank: RankTier | None
    """R — `None` below Standard's threshold (sheet shows a blank cell)."""
    referral_role: ReferralTier | None
    """S — `None` below Scout's threshold."""
    breakdown: ProgressionBreakdown


def _ceil_div(numerator: int, denominator: int) -> int:
    return -(-numerator // denominator)


def compute_progression(
    aggregates: PlayerAggregates,
    *,
    ranks: RankLadder | None = None,
    referrals: ReferralLadder | None = None,
) -> PlayerProgression:
    """Compute one player's full progression, term-for-term matching K3/L3.

    Args:
        aggregates: Pre-resolved per-player aggregates (see `PlayerAggregates`).
        ranks: Rank ladder to resolve `R` against; defaults to the canonical
            `RankLadder()`. Overridable for tests that need a different ladder.
        referrals: Referral-role ladder to resolve `S` against; defaults to
            the canonical `ReferralLadder()`.
    """
    ranks = ranks if ranks is not None else RankLadder()
    referrals = referrals if referrals is not None else ReferralLadder()

    m = aggregates.purchase_turnover
    n = aggregates.sale_turnover
    total_turnover = m + n

    referral_role = referrals.current(aggregates.referral_count)

    base_coins = m // PURCHASE_TURNOVER_PER_COIN + n // SALE_TURNOVER_PER_COIN
    base_xp = base_coins * XP_PER_COIN_MILESTONE

    rt = aggregates.referee_total_turnover
    referee_turnover_coins = sum(
        coins for threshold, coins, _xp in REFEREE_TURNOVER_TIERS if rt >= threshold
    )
    referee_turnover_xp = sum(
        xp for threshold, _coins, xp in REFEREE_TURNOVER_TIERS if rt >= threshold
    )

    referred_coins, referred_xp = 0, 0
    if aggregates.has_referrer:
        for m_threshold, n_threshold, coins, xp in REFERRED_TIERS:
            if m >= m_threshold or n >= n_threshold:
                referred_coins, referred_xp = coins, xp
                break

    referral_role_coins, referral_role_xp = (
        REFERRAL_ONE_TIME[referral_role.key] if referral_role is not None else (0, 0)
    )

    if aggregates.is_booster:
        booster_coins = BOOSTER_FLAT[0] + aggregates.booster_big_deal_count
        booster_xp = BOOSTER_FLAT[1] + 5 * aggregates.booster_big_deal_count
    else:
        booster_coins, booster_xp = 0, 0

    pre_xp = base_xp + referee_turnover_xp + referred_xp + referral_role_xp + booster_xp
    xp_boost_applied = base_xp >= XP_BOOST_THRESHOLD
    xp = (
        _ceil_div(pre_xp * _XP_BOOST_NUMERATOR, _XP_BOOST_DENOMINATOR)
        if xp_boost_applied
        else pre_xp
    )

    rank = ranks.current(xp)
    rank_one_time_coins = RANK_ONE_TIME_COINS[rank.key] if rank is not None else 0

    big_deal_coins = 0
    if rank is not None:
        if rank.key in _BIG_DEAL_100M_RANKS:
            big_deal_coins += 5 * aggregates.deal_count_over_100m
        if rank.key == _BIG_DEAL_50M_RANK:
            big_deal_coins += 2 * aggregates.deal_count_over_50m

    coins = (
        base_coins
        + aggregates.coin_ledger_delta
        + referee_turnover_coins
        + referred_coins
        + rank_one_time_coins
        + referral_role_coins
        + booster_coins
        + big_deal_coins
    )

    breakdown = ProgressionBreakdown(
        base_coins=base_coins,
        base_xp=base_xp,
        referee_turnover_coins=referee_turnover_coins,
        referee_turnover_xp=referee_turnover_xp,
        referred_coins=referred_coins,
        referred_xp=referred_xp,
        rank_one_time_coins=rank_one_time_coins,
        referral_role_coins=referral_role_coins,
        referral_role_xp=referral_role_xp,
        booster_coins=booster_coins,
        booster_xp=booster_xp,
        big_deal_coins=big_deal_coins,
        ledger_delta=aggregates.coin_ledger_delta,
        xp_boost_applied=xp_boost_applied,
    )

    return PlayerProgression(
        coins=coins,
        xp=xp,
        purchase_turnover=m,
        sale_turnover=n,
        total_turnover=total_turnover,
        referral_count=aggregates.referral_count,
        rank=rank,
        referral_role=referral_role,
        breakdown=breakdown,
    )


__all__ = [
    "CALCULATOR_VERSION",
    "DealReward",
    "PlayerAggregates",
    "PlayerProgression",
    "ProgressionBreakdown",
    "compute_progression",
    "deal_reward",
]
