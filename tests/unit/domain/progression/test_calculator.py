"""Direct unit tests for `domain.progression.calculator`.

`test_sheet_parity.py` proves the module against 250 real players, but the
live book currently has zero boosters and nobody past Standard rank
(sqlite_migration.md §I.1) — this file exercises the branches real data
can't reach yet: booster bonuses, and the Prestige/Elite/Legend big-deal
terms.
"""

from dataclasses import replace

from stalbot.domain.enums import DealType
from stalbot.domain.progression.calculator import (
    PlayerAggregates,
    compute_progression,
    deal_reward,
)

_ZERO = PlayerAggregates(
    purchase_turnover=0,
    sale_turnover=0,
    coin_ledger_delta=0,
    referral_count=0,
    referee_total_turnover=0,
    has_referrer=False,
    is_booster=False,
    booster_big_deal_count=0,
    deal_count_over_50m=0,
    deal_count_over_100m=0,
)


def _replace(**overrides: object) -> PlayerAggregates:
    return replace(_ZERO, **overrides)  # type: ignore[arg-type]


def test_deal_reward_purchase_and_sale_use_different_divisors() -> None:
    assert deal_reward(DealType.PURCHASE, 3_000_000) == deal_reward(DealType.PURCHASE, 3_000_000)
    purchase = deal_reward(DealType.PURCHASE, 3_000_000)
    sale = deal_reward(DealType.SALE, 3_000_000)
    assert purchase.coins == 2  # 3M // 1.5M
    assert purchase.xp == 20
    assert sale.coins == 1  # 3M // 2.5M
    assert sale.xp == 10


def test_deal_reward_below_threshold_is_zero() -> None:
    assert deal_reward(DealType.PURCHASE, 1_000_000) == deal_reward(DealType.SALE, 1_000_000)
    assert deal_reward(DealType.PURCHASE, 1_000_000).coins == 0


def test_no_activity_player_has_no_rank_or_role() -> None:
    progression = compute_progression(_ZERO)
    assert progression.coins == 0
    assert progression.xp == 0
    assert progression.rank is None
    assert progression.referral_role is None


def test_xp_boost_applies_once_base_xp_crosses_250() -> None:
    below = compute_progression(_replace(purchase_turnover=37_000_000))  # base_xp 240
    at = compute_progression(_replace(purchase_turnover=39_000_000))  # base_xp 260 -> boosted

    assert below.breakdown.xp_boost_applied is False
    assert below.xp == 240

    assert at.breakdown.xp_boost_applied is True
    # ceil(260 * 1.05) == 273, not floor (273.0 exactly here, so also
    # exercised by test_xp_boost_rounds_up_not_down below).
    assert at.xp == 273


def test_xp_boost_rounds_up_not_down() -> None:
    # base_xp = 250 exactly -> pre_xp*1.05 = 262.5, must ceil to 263.
    progression = compute_progression(_replace(purchase_turnover=37_500_000))
    assert progression.breakdown.base_xp == 250
    assert progression.xp == 263


def test_booster_flat_bonus_and_big_deal_surcharge() -> None:
    no_big_deals = compute_progression(_replace(is_booster=True, booster_big_deal_count=0))
    assert no_big_deals.breakdown.booster_coins == 3
    assert no_big_deals.breakdown.booster_xp == 30

    with_big_deals = compute_progression(_replace(is_booster=True, booster_big_deal_count=2))
    assert with_big_deals.breakdown.booster_coins == 3 + 2
    assert with_big_deals.breakdown.booster_xp == 30 + 5 * 2


def test_referee_turnover_tiers_are_additive() -> None:
    tier1 = compute_progression(_replace(referee_total_turnover=1_500_000))
    tier2 = compute_progression(_replace(referee_total_turnover=5_000_000))
    tier3 = compute_progression(_replace(referee_total_turnover=50_000_000))

    assert (tier1.breakdown.referee_turnover_coins, tier1.breakdown.referee_turnover_xp) == (3, 20)
    assert (tier2.breakdown.referee_turnover_coins, tier2.breakdown.referee_turnover_xp) == (
        3 + 2,
        20 + 20,
    )
    assert (tier3.breakdown.referee_turnover_coins, tier3.breakdown.referee_turnover_xp) == (
        3 + 2 + 10,
        20 + 20 + 80,
    )


def test_referred_bonus_is_highest_matching_tier_not_additive() -> None:
    no_referrer = compute_progression(_replace(has_referrer=False, sale_turnover=200_000_000))
    assert (no_referrer.breakdown.referred_coins, no_referrer.breakdown.referred_xp) == (0, 0)

    with_referrer = compute_progression(_replace(has_referrer=True, sale_turnover=200_000_000))
    # N=200M clears the top tier (N>=125M) -> single highest tier (15, 200),
    # not the sum of all three tiers it also clears.
    assert (with_referrer.breakdown.referred_coins, with_referrer.breakdown.referred_xp) == (
        15,
        200,
    )


def test_prestige_big_deal_bonus_only_fires_at_prestige_exactly() -> None:
    # 1200 XP lands exactly on Prestige (ranks.py threshold).
    prestige = compute_progression(
        _replace(purchase_turnover=180_000_000, deal_count_over_50m=3, deal_count_over_100m=3)
    )
    assert prestige.rank is not None
    assert prestige.rank.key == "prestige"
    # Prestige gets the 50m term (2 * 3 = 6) but NOT the 100m term (that's
    # Elite/Legend-only, a literal equality in the source formula).
    assert prestige.breakdown.big_deal_coins == 6


def test_elite_and_legend_big_deal_bonus_uses_100m_not_50m() -> None:
    # base_xp from a huge purchase turnover alone easily clears Elite (3500).
    elite = compute_progression(
        _replace(purchase_turnover=530_000_000, deal_count_over_50m=5, deal_count_over_100m=4)
    )
    assert elite.rank is not None
    assert elite.rank.key == "elite"
    # Elite/Legend only get the 100m term (5 * 4 = 20), never the 50m term.
    assert elite.breakdown.big_deal_coins == 20


def test_rank_and_referral_role_one_time_bonuses_are_summed_into_coins() -> None:
    progression = compute_progression(_replace(purchase_turnover=7_500_000, referral_count=1))
    assert progression.rank is not None
    assert progression.rank.key == "standard"
    assert progression.referral_role is not None
    assert progression.referral_role.key == "scout"
    assert progression.breakdown.rank_one_time_coins == 5  # RANK_ONE_TIME_COINS["standard"]
    assert progression.breakdown.referral_role_coins == 1  # REFERRAL_ONE_TIME["scout"][0]


def test_coin_ledger_delta_can_make_coins_negative_after_shop_spend() -> None:
    progression = compute_progression(_replace(coin_ledger_delta=-50))
    assert progression.coins == -50
