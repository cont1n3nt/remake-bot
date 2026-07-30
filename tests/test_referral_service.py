"""Baseline-фиксирующие тесты для bot/services/referral_service.py.

⚠ ЛОВУШКА (см. REFACTORING_PLAN.md, Фаза D): RANK_THRESHOLDS в этом модуле —
пороги XP (список), НЕ пороги оборота в рублях из bot/config/constants.py /
bot/services/role_service.py. Не путать со значениями в test_role_service.py.
"""
from bot.services.referral_service import ReferralService

rs = ReferralService(repo=None)  # чистые методы репозиторий не используют


# --- get_referral_level (пороги количества рефералов: [1, 5, 10, 25, 100]) ---

def test_referral_level_zero():
    assert rs.get_referral_level(0) == 0


def test_referral_level_one():
    assert rs.get_referral_level(1) == 1


def test_referral_level_below_next_threshold():
    assert rs.get_referral_level(4) == 1


def test_referral_level_at_second_threshold():
    assert rs.get_referral_level(5) == 2


def test_referral_level_below_max_threshold():
    assert rs.get_referral_level(49) == 4
    assert rs.get_referral_level(50) == 4


def test_referral_level_far_above_max_threshold():
    assert rs.get_referral_level(500000) == 5


def test_referral_level_negative():
    assert rs.get_referral_level(-5) == 0


# --- get_target_referral_name (то же самое, но возвращает имя роли) ---

def test_target_referral_name_zero_is_none():
    assert ReferralService.get_target_referral_name(0) is None


def test_target_referral_name_one():
    assert ReferralService.get_target_referral_name(1) == "Скаут"


def test_target_referral_name_four():
    assert ReferralService.get_target_referral_name(4) == "Скаут"


def test_target_referral_name_five():
    assert ReferralService.get_target_referral_name(5) == "Промоутер"


def test_target_referral_name_forty_nine():
    assert ReferralService.get_target_referral_name(49) == "Амбассадор"


def test_target_referral_name_fifty():
    assert ReferralService.get_target_referral_name(50) == "Амбассадор"


def test_target_referral_name_max():
    assert ReferralService.get_target_referral_name(500000) == "Рекламный Барон"


def test_target_referral_name_negative_is_none():
    assert ReferralService.get_target_referral_name(-5) is None


# --- get_target_rank_name (пороги XP: [50, 250, 1000, 5000, 10000]) ---

def test_target_rank_name_zero_is_none():
    assert ReferralService.get_target_rank_name(0) is None


def test_target_rank_name_below_first_threshold_is_none():
    assert ReferralService.get_target_rank_name(49) is None


def test_target_rank_name_at_first_threshold():
    assert ReferralService.get_target_rank_name(50) == "🔹 Standard"


def test_target_rank_name_at_second_threshold():
    assert ReferralService.get_target_rank_name(250) == "🔷 Premium"


def test_target_rank_name_below_third_threshold():
    assert ReferralService.get_target_rank_name(999) == "🔷 Premium"


def test_target_rank_name_at_third_threshold():
    assert ReferralService.get_target_rank_name(1000) == "💠 Prestige"


def test_target_rank_name_at_fourth_threshold():
    assert ReferralService.get_target_rank_name(5000) == "💎 Elite"


def test_target_rank_name_at_max_threshold():
    assert ReferralService.get_target_rank_name(10000) == "👑 Legend"


def test_target_rank_name_far_above_max():
    assert ReferralService.get_target_rank_name(500000) == "👑 Legend"


def test_target_rank_name_negative_is_none():
    assert ReferralService.get_target_rank_name(-5) is None


# --- get_rank_progress (прогресс-бар до следующего ранга по XP) ---

def test_rank_progress_zero():
    assert rs.get_rank_progress(0) == (0, 50, "🔹 Standard")


def test_rank_progress_mid_level():
    assert rs.get_rank_progress(5000) == (5000, 10000, "👑 Legend")


def test_rank_progress_at_max_returns_none():
    # На максимальном ранге (Legend, xp >= 10000) прогресс не показывается.
    assert rs.get_rank_progress(10000) is None


def test_rank_progress_negative_xp():
    # ЛОВУШКА: отрицательный xp не отклоняется, попадает в ветку "до Standard".
    assert rs.get_rank_progress(-5) == (-5, 50, "🔹 Standard")
