"""Baseline-фиксирующие тесты для bot/services/role_service.py.

⚠ ЛОВУШКА (см. REFACTORING_PLAN.md, Фаза D): RoleService.get_target_rank_name
работает с ДРУГИМ набором порогов, чем ReferralService.get_target_rank_name
из test_referral_service.py — здесь пороги в рублях оборота
({"Standard": 0, "Premium": 5000, ...}), там — пороги XP ([50, 250, ...]).
Одинаковое имя метода, разный смысл. Ниже тесты используют разные входные
величины (объём сделок в рублях, а не XP) сознательно, чтобы не создать
ложное впечатление совпадения семантики.
"""
from bot.services.role_service import RoleService


# --- D.1: role_service больше не хранит свои копии констант, а реэкспортирует
#     объекты из bot/config/constants.py ---

def test_role_service_constants_are_constants_module_objects():
    import bot.config.constants as constants
    import bot.services.role_service as role_service

    assert role_service.RANK_THRESHOLDS is constants.RANK_THRESHOLDS
    assert role_service.RANK_ROLES is constants.RANK_ROLES
    assert role_service.REFERRAL_THRESHOLDS is constants.REFERRAL_THRESHOLDS
    assert role_service.REFERRAL_ROLES is constants.REFERRAL_ROLES


# --- get_target_rank_name (пороги ОБОРОТА В РУБЛЯХ: Standard=0, Premium=5000,
#     Prestige=25000, Elite=100000, Legend=500000) ---

def test_rank_name_zero_volume_is_standard():
    # Порог Standard равен 0 — в отличие от XP-версии, ноль уже даёт роль.
    assert RoleService.get_target_rank_name(0) == "Standard"


def test_rank_name_below_premium():
    assert RoleService.get_target_rank_name(4999) == "Standard"


def test_rank_name_at_premium():
    assert RoleService.get_target_rank_name(5000) == "Premium"


def test_rank_name_at_prestige():
    assert RoleService.get_target_rank_name(25000) == "Prestige"


def test_rank_name_at_elite():
    assert RoleService.get_target_rank_name(100000) == "Elite"


def test_rank_name_at_legend():
    assert RoleService.get_target_rank_name(500000) == "Legend"


def test_rank_name_far_above_legend():
    assert RoleService.get_target_rank_name(999999999) == "Legend"


def test_rank_name_negative_volume_is_none():
    # В отличие от нулевого объёма, отрицательный не удовлетворяет даже
    # порогу Standard (0), поэтому роль не назначается.
    assert RoleService.get_target_rank_name(-100) is None


# --- get_target_referral_name (пороги количества рефералов — та же шкала,
#     что и в ReferralService: [1, 5, 10, 25, 100]) ---

def test_referral_name_zero_is_none():
    assert RoleService.get_target_referral_name(0) is None


def test_referral_name_one():
    assert RoleService.get_target_referral_name(1) == "Скаут"


def test_referral_name_four():
    assert RoleService.get_target_referral_name(4) == "Скаут"


def test_referral_name_five():
    assert RoleService.get_target_referral_name(5) == "Промоутер"


def test_referral_name_forty_nine():
    assert RoleService.get_target_referral_name(49) == "Амбассадор"


def test_referral_name_fifty():
    assert RoleService.get_target_referral_name(50) == "Амбассадор"


def test_referral_name_max():
    assert RoleService.get_target_referral_name(500000) == "Рекламный Барон"


def test_referral_name_negative_is_none():
    assert RoleService.get_target_referral_name(-5) is None


def test_referral_name_matches_referral_service_for_same_counts():
    # Задокументировать (а не предполагать), что пороги рефералов в
    # RoleService и ReferralService СОВПАДАЮТ (в отличие от рангов выше) —
    # см. AUDIT.md §7.1 п.1: это дублирование одного и того же набора
    # констант, а не два разных бизнес-правила под одинаковым именем.
    from bot.services.referral_service import ReferralService

    for count in (0, 1, 4, 5, 49, 50, 500000, -5):
        assert RoleService.get_target_referral_name(count) == \
            ReferralService.get_target_referral_name(count)
