"""Tests for `stalbot.domain.progression.perks` (formula-verified bonus text, PLAN.md §9.1.1)."""

import pytest

from stalbot.domain.progression.perks import (
    BOOSTER_ONE_TIME_TEXT,
    PURCHASE_TURNOVER_PER_COIN,
    SALE_TURNOVER_PER_COIN,
    XP_BOOST_PERCENT,
    XP_BOOST_THRESHOLD,
    XP_PER_COIN_MILESTONE,
    rank_perks,
    referral_perks,
)


@pytest.mark.parametrize(
    ("key", "coins"),
    [("standard", 5), ("premium", 10), ("prestige", 40), ("elite", 100), ("legend", 200)],
)
def test_rank_one_time_bonus_matches_canon(key: str, coins: int) -> None:
    assert rank_perks(key)[0] == f"🎁 Разовый бонус: 🪙 {coins} Coins"


def test_standard_and_premium_have_no_big_deal_bonus() -> None:
    assert len(rank_perks("standard")) == 1
    assert len(rank_perks("premium")) == 1


def test_prestige_big_deal_bonus() -> None:
    assert rank_perks("prestige")[1] == "🔥 Крупные сделки: 🪙 2 Coins за сделку свыше 50 000 000 ₽"


def test_elite_and_legend_share_the_same_big_deal_bonus() -> None:
    assert rank_perks("elite")[1] == rank_perks("legend")[1]
    assert rank_perks("elite")[1] == "🔥 Крупные сделки: 🪙 5 Coins за сделку свыше 100 000 000 ₽"


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("scout", "🎁 Разовый бонус: 🪙 1 Coins"),
        ("promoter", "🎁 Разовый бонус: 🪙 5 Coins + ⚡ 10 XP"),
        ("recruiter", "🎁 Разовый бонус: 🪙 15 Coins"),
        ("ambassador", "🎁 Разовый бонус: 🪙 40 Coins + ⚡ 60 XP"),
        ("baron", "🎁 Разовый бонус: 🪙 150 Coins"),
    ],
)
def test_referral_one_time_bonus_matches_canon(key: str, expected: str) -> None:
    assert referral_perks(key) == (expected,)


def test_global_canon_numbers_match_the_formula() -> None:
    assert PURCHASE_TURNOVER_PER_COIN == 1_500_000
    assert SALE_TURNOVER_PER_COIN == 2_500_000
    assert XP_PER_COIN_MILESTONE == 10
    assert BOOSTER_ONE_TIME_TEXT == "🚀 Буст сервера, разово: 🪙 3 + ⚡ 30"
    assert XP_BOOST_THRESHOLD == 250
    assert XP_BOOST_PERCENT == 5
