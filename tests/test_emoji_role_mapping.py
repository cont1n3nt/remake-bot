"""Регрессионный тест для Этапа D.4 (REFACTORING_PLAN.md, Фаза D).

bot/cogs/transactions.py::RANK_ROLES/REFERRAL_ROLES (словари с
эмодзи-ключами вида "🔹 Standard", напрямую сопоставленные с ID
Discord-ролей) заменены на RANK_ROLES_BY_LABEL/REFERRAL_ROLES_BY_LABEL —
производные словари, построенные в bot/config/constants.py из
RANK_ROLES/REFERRAL_ROLES (единый источник ID, см. Этап D.1) и
RANK_EMOJI_LABELS/REFERRAL_EMOJI_LABELS (таблица "эмодзи-метка → plain-имя").

Ниже — побайтовое сравнение всех ключей и значений старого (жёстко
закодированного здесь, независимо от constants.py) и нового словаря.

⚠ Статус верификации меток эмодзи против живой Google Таблицы (важно для
любого, кто будет менять эти строки в будущем): пользователь подтвердил
вручную по колонкам R/S только "🔹 Standard", "🔷 Premium", "🧭 Скаут",
"📣 Промоутер" (2026-07-30). Остальные пять меток (Prestige/Elite/Legend,
Вербовщик/Амбассадор/Рекламный Барон) перенесены как есть из уже
существовавшего кода `transactions.py`, без отдельной сверки с таблицей.
"""
from bot.config.constants import RANK_ROLES_BY_LABEL, REFERRAL_ROLES_BY_LABEL

# Эталон — то, что было захардкожено в bot/cogs/transactions.py до Этапа D.4.
_OLD_RANK_ROLES: dict[str, int] = {
    "🔹 Standard": 1518324856549277827,
    "🔷 Premium": 1518328036137631805,
    "💠 Prestige": 1518328037631066232,
    "💎 Elite": 1518328222939611166,
    "👑 Legend": 1518328324605083698,
}

_OLD_REFERRAL_ROLES: dict[str, int] = {
    "🧭 Скаут": 1518583879672270878,
    "📣 Промоутер": 1518584176054636584,
    "🧲 Вербовщик": 1518584268933300274,
    "📢 Амбассадор": 1518584424818671687,
    "🎩 Рекламный Барон": 1518584494410563625,
}


def test_rank_roles_by_label_matches_old_hardcoded_dict():
    assert RANK_ROLES_BY_LABEL == _OLD_RANK_ROLES


def test_referral_roles_by_label_matches_old_hardcoded_dict():
    assert REFERRAL_ROLES_BY_LABEL == _OLD_REFERRAL_ROLES


def test_rank_roles_by_label_keys_and_values_are_byte_identical():
    for key, value in _OLD_RANK_ROLES.items():
        assert key in RANK_ROLES_BY_LABEL, f"ключ {key!r} отсутствует в новом словаре"
        assert RANK_ROLES_BY_LABEL[key] == value


def test_referral_roles_by_label_keys_and_values_are_byte_identical():
    for key, value in _OLD_REFERRAL_ROLES.items():
        assert key in REFERRAL_ROLES_BY_LABEL, f"ключ {key!r} отсутствует в новом словаре"
        assert REFERRAL_ROLES_BY_LABEL[key] == value
