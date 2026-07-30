"""Регрессионный тест для Этапа D.3 (REFACTORING_PLAN.md, Фаза D).

D.3 заменяет захардкоженный список `THRESHOLDS = [1, 5, 10, 25, 100]` в
bot/services/referral_service.py на `list(REFERRAL_THRESHOLDS.values())`,
импортированный из bot/config/constants.py. Риск — по плану он "средний":
соответствие между полученным списком порогов и параллельным списком
`REF_ROLE_NAMES` держится на неявном совпадении порядка вставки в словарь
`REFERRAL_THRESHOLDS`, а не на явной связи по ключу.

Здесь эталон ("oracle") — независимая копия исходных чисел, зафиксированных
ДО правки (не импортируется из referral_service.py, чтобы тест не стал
тавтологией после самой правки). Полный перебор count от 0 до 200
сверяется с этим эталоном и до, и после Этапа D.3 — числа должны совпадать
100%.
"""
from bot.services.referral_service import ReferralService

# Эталон, зафиксированный до Этапа D.3 (см. REFACTOR_PROGRESS.md, D.3).
_ORACLE_THRESHOLDS = [1, 5, 10, 25, 100]
_ORACLE_NAMES = ["Скаут", "Промоутер", "Вербовщик", "Амбассадор", "Рекламный Барон"]


def _oracle_level(count: int) -> int:
    level = 0
    for i, threshold in enumerate(_ORACLE_THRESHOLDS):
        if count >= threshold:
            level = i + 1
        else:
            break
    return level


def _oracle_name(count: int):
    target = None
    for i, threshold in enumerate(_ORACLE_THRESHOLDS):
        if count >= threshold:
            target = _ORACLE_NAMES[i]
    return target


def test_referral_level_matches_oracle_for_full_range():
    rs = ReferralService(repo=None)
    for count in range(0, 201):
        assert rs.get_referral_level(count) == _oracle_level(count), count


def test_target_referral_name_matches_oracle_for_full_range():
    for count in range(0, 201):
        assert ReferralService.get_target_referral_name(count) == _oracle_name(count), count
