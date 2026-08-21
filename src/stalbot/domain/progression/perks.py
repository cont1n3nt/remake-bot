"""Perk text for each rank/referral tier (see PLAN.md §9.1.1, decision A6).

Every number here is verified against the sheet's own `K3`/`L3` (Coins/XP)
formulas captured live from the spreadsheet, not transcribed from the
original text description (which disagreed with the formulas in several
places — decision A6: the formula always wins). Only formula-backed bonuses
are included; the illustrative discount-percentage/priority-queue copy
shown in PLAN.md §10.2's `/profile` mockup is not confirmed per-tier by any
formula, so it is deliberately left for M5 to source for real rather than
invented here.
"""

from typing import Final

# --- one-time bonuses, verified against the K3 formula's rank/role IF-chains ---
# Published (sqlite_migration.md §V.1) for reuse by `domain.progression.calculator`
# — previously private and unused anywhere outside this module.

RANK_ONE_TIME_COINS: Final = {
    "standard": 5,
    "premium": 10,
    "prestige": 40,
    "elite": 100,
    "legend": 200,
}

REFERRAL_ONE_TIME: Final = {
    "scout": (1, 0),
    "promoter": (5, 10),
    "recruiter": (15, 0),
    "ambassador": (40, 60),
    "baron": (150, 0),
}

# --- big-deal bonus, verified against the SUMPRODUCT tail of the K3 formula ---
# Prestige: +2 Coins per single deal >= 50,000,000 ₽. Elite and Legend: +5
# Coins per single deal >= 100,000,000 ₽. Standard and Premium: none.
BIG_DEAL_BONUS: Final = {
    "prestige": (2, 50_000_000),
    "elite": (5, 100_000_000),
    "legend": (5, 100_000_000),
}


def rank_perks(key: str) -> tuple[str, ...]:
    """Build the ready-to-display perk lines for a rank tier.

    Args:
        key: A `RankTier.key`.
    """
    lines = [f"🎁 Разовый бонус: 🪙 {RANK_ONE_TIME_COINS[key]} Coins"]
    if key in BIG_DEAL_BONUS:
        coins, threshold = BIG_DEAL_BONUS[key]
        lines.append(
            f"🔥 Крупные сделки: 🪙 {coins} Coins за сделку свыше {threshold:,} ₽".replace(",", " ")
        )
    return tuple(lines)


def referral_perks(key: str) -> tuple[str, ...]:
    """Build the ready-to-display perk lines for a referral-role tier.

    Args:
        key: A `ReferralTier.key`.
    """
    coins, xp = REFERRAL_ONE_TIME[key]
    if xp:
        return (f"🎁 Разовый бонус: 🪙 {coins} Coins + ⚡ {xp} XP",)
    return (f"🎁 Разовый бонус: 🪙 {coins} Coins",)


# --- canon numbers that apply globally, not to one specific tier ---
# Not consumed by ProgressionService (they aren't tied to a promotion
# event); kept here so M5's /profile sources them from the same place as
# everything else instead of re-deriving them from the formula by hand.

#: Turnover-to-currency conversion rate, verified against the K3/L3
#: formulas' `TRUNC(M3/1500000)`/`TRUNC(N3/2500000)` terms: every this many
#: ₽ of purchase/sale turnover is worth 1 Coin (+10 XP alongside it).
PURCHASE_TURNOVER_PER_COIN: Final = 1_500_000
SALE_TURNOVER_PER_COIN: Final = 2_500_000
XP_PER_COIN_MILESTONE: Final = 10

#: One-time bonus for boosting the server, verified against the K3
#: formula's `IF(Q3=TRUE; 3 + ...; 0)` term (the `...` is the big-deal
#: bonus in `perks_of`, not part of this flat one-time amount).
BOOSTER_ONE_TIME_TEXT: Final = "🚀 Буст сервера, разово: 🪙 3 + ⚡ 30"

#: Verified against the L3 formula's trailing
#: `IF((TRUNC(M3/1500000)+TRUNC(N3/2500000))*10>=250; 1.05; 1)` multiplier:
#: once base XP (before any bonuses) reaches this, XP gains get a flat
#: bonus — this triggers before Premium's 300 XP threshold, not "at" it.
XP_BOOST_THRESHOLD: Final = 250
XP_BOOST_PERCENT: Final = 5

# --- reward tiers consumed by `domain.progression.calculator` (sqlite_migration.md §V.1) ---
# Verified against the K3/L3 formulas' SUMIF/IF-chain terms over the
# *referrer's* referred-players' combined turnover (`rt` in the plan's
# pseudocode) — additive: every tier whose threshold `rt` clears adds its
# (coins, xp) on top of the ones below it.
#: (rt_threshold, coins, xp)
REFEREE_TURNOVER_TIERS: Final = (
    (1_500_000, 3, 20),
    (5_000_000, 2, 20),
    (50_000_000, 10, 80),
)

# Verified against the same K3/L3 terms, but over the *referred player's
# own* M/N turnover (not `rt`) — a single highest-matching tier, not
# additive: "пришёл по рефералке" pays out once, at whichever tier the
# referred player's own purchase/sale turnover clears.
#: (m_threshold, n_threshold, coins, xp)
REFERRED_TIERS: Final = (
    (50_000_000, 125_000_000, 15, 200),
    (5_000_000, 12_500_000, 3, 40),
    (1_500_000, 2_500_000, 2, 10),
)

#: Flat one-time (coins, xp) for boosting the server — same numbers as
#: `BOOSTER_ONE_TIME_TEXT`, published as data for `calculator.py`.
BOOSTER_FLAT: Final = (3, 30)

#: A booster's per-deal surcharge fires once a single deal clears either
#: threshold: purchase >= the first, sale >= the second (K3's
#: `($E>=10000000)+($E>=25000000)` SUMPRODUCT term, gated on deal side).
BOOSTER_BIG_DEAL_THRESHOLDS: Final = (10_000_000, 25_000_000)
