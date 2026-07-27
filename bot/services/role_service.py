"""Сервис управления Discord-ролями: расчёт, назначение, снятие, логирование."""

import json
import logging
import os
from typing import Optional

import discord

logger = logging.getLogger("bot")

# ------------------------------------------------------------------ #
#  Конфигурация ранговых ролей                                      #
#  Порог — минимальный объём сделок для получения роли               #
# ------------------------------------------------------------------ #

RANK_THRESHOLDS: dict[str, int] = {
    "Standard": 0,
    "Premium": 5000,
    "Prestige": 25000,
    "Elite": 100000,
    "Legend": 500000,
}

RANK_ROLES: dict[str, int] = {
    "Standard": 1518324856549277827,
    "Premium": 1518328036137631805,
    "Prestige": 1518328037631066232,
    "Elite": 1518328222939611166,
    "Legend": 1518328324605083698,
}

# ------------------------------------------------------------------ #
#  Конфигурация реферальных ролей                                   #
#  Порог — количество приглашённых пользователей                     #
# ------------------------------------------------------------------ #

REFERRAL_THRESHOLDS: dict[str, int] = {
    "Скаут": 1,
    "Промоутер": 5,
    "Вербовщик": 10,
    "Амбассадор": 25,
    "Рекламный Барон": 100,
}

REFERRAL_ROLES: dict[str, int] = {
    "Скаут": 1518583879672270878,
    "Промоутер": 1518584176054636584,
    "Вербовщик": 1518584268933300274,
    "Амбассадор": 1518584424818671687,
    "Рекламный Барон": 1518584494410563625,
}

REFERRALS_FILE = "referrals.json"


class RoleService:
    """Расчёт целевых ролей, назначение/снятие на сервере, учёт рефералов."""

    def __init__(self) -> None:
        self._bot: Optional[discord.Client] = None
        self._referrals: dict[str, int] = {}
        self._load_referrals()

    def set_bot(self, bot: discord.Client) -> None:
        """Привязка к боту (вызывается после инициализации кога)."""
        self._bot = bot

    # ------------------------------------------------------------------ #
    #  Персистентность рефералов (JSON)                                  #
    # ------------------------------------------------------------------ #

    def _load_referrals(self) -> None:
        if os.path.exists(REFERRALS_FILE):
            try:
                with open(REFERRALS_FILE, encoding="utf-8") as f:
                    self._referrals = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._referrals = {}

    def _save_referrals(self) -> None:
        with open(REFERRALS_FILE, "w", encoding="utf-8") as f:
            json.dump(self._referrals, f, ensure_ascii=False, indent=2)

    def get_referral_count(self, user_id: int) -> int:
        """Количество рефералов пользователя по его Discord ID."""
        return self._referrals.get(str(user_id), 0)

    def increment_referral(self, user_id: int) -> int:
        """Увеличить счётчик рефералов и сохранить. Возвращает новое значение."""
        key = str(user_id)
        count = self._referrals.get(key, 0) + 1
        self._referrals[key] = count
        self._save_referrals()
        return count

    # ------------------------------------------------------------------ #
    #  Определение целевой роли по порогам                               #
    # ------------------------------------------------------------------ #

    @staticmethod
    def get_target_rank_name(volume: int | float) -> Optional[str]:
        """Определить название ранговой роли по сумме сделок."""
        target: Optional[str] = None
        for name, threshold in sorted(RANK_THRESHOLDS.items(), key=lambda x: x[1]):
            if volume >= threshold:
                target = name
        return target

    @staticmethod
    def get_target_referral_name(count: int) -> Optional[str]:
        """Определить название реферальной роли по числу приглашённых."""
        target: Optional[str] = None
        for name, threshold in sorted(REFERRAL_THRESHOLDS.items(), key=lambda x: x[1]):
            if count >= threshold:
                target = name
        return target

    # ------------------------------------------------------------------ #
    #  Назначение ролей на сервере                                       #
    # ------------------------------------------------------------------ #

    async def assign_rank_role(
        self, member: discord.Member, volume: int | float,
    ) -> Optional[str]:
        """Назначить ранговую роль по объёму сделок. Вернуть название роли или None."""
        return await self._sync_role(
            member=member,
            target_name=self.get_target_rank_name(volume),
            role_map=RANK_ROLES,
            group_label="Rank",
        )

    async def assign_referral_role(
        self, member: discord.Member, count: int,
    ) -> Optional[str]:
        """Назначить реферальную роль по числу рефералов."""
        return await self._sync_role(
            member=member,
            target_name=self.get_target_referral_name(count),
            role_map=REFERRAL_ROLES,
            group_label="Referral",
        )

    async def set_specific_role(
        self, member: discord.Member, role_name: str, role_map: dict[str, int],
        group_label: str,
    ) -> Optional[str]:
        """Принудительно установить конкретную роль из role_map (для /set_* команд)."""
        return await self._sync_role(member, role_name, role_map, group_label)

    async def _sync_role(
        self,
        member: discord.Member,
        target_name: Optional[str],
        role_map: dict[str, int],
        group_label: str,
    ) -> Optional[str]:
        """Синхронизировать роли участника: снять все старые, надеть целевую."""
        target_id = role_map.get(target_name) if target_name else None

        # Определяем, какая роль из группы уже надета
        current_role = self._get_current_from_map(member, role_map)

        # Если уже стоит нужная — ничего не делаем
        if current_role and target_id and current_role.id == target_id:
            return None

        # Снимаем все роли этой группы
        for rid in role_map.values():
            role_obj = member.guild.get_role(rid)
            if role_obj and role_obj in member.roles:
                try:
                    await member.remove_roles(role_obj, reason=f"Auto {group_label} sync")
                except (discord.Forbidden, discord.HTTPException):
                    pass

        # Надеваем новую роль (если есть)
        if target_id:
            role_obj = member.guild.get_role(target_id)
            if role_obj is None:
                logger.warning("Роль ID %s (group=%s) не найдена на сервере", target_id, group_label)
                return None
            try:
                await member.add_roles(role_obj, reason=f"Auto {group_label}")
            except discord.Forbidden:
                logger.warning("Нет прав на назначение роли %s пользователю %s", role_obj.name, member)
                return None
            except discord.HTTPException as e:
                logger.error("Ошибка назначения роли %s: %s", role_obj.name, e)
                return None

        # Логируем смену
        old_name = self._get_name_by_id(current_role.id, role_map) if current_role else "(нет)"
        new_name = target_name or "(нет)"
        logger.info("[ROLE] %s | %s: %s → %s", member.id, group_label, old_name, new_name)
        return target_name

    # ------------------------------------------------------------------ #
    #  Утилиты                                                           #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _get_current_from_map(member: discord.Member, role_map: dict[str, int]) -> Optional[discord.Role]:
        """Вернуть объект роли участника, если она есть в role_map."""
        member_ids = {r.id for r in member.roles}
        for rid in role_map.values():
            if rid in member_ids:
                guild = member.guild
                return guild.get_role(rid) if guild else None
        return None

    @staticmethod
    def _get_name_by_id(role_id: int, role_map: dict[str, int]) -> Optional[str]:
        for name, rid in role_map.items():
            if rid == role_id:
                return name
        return None
