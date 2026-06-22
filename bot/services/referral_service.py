from typing import Optional

from bot.models.user import User
from bot.repositories.sheets_repository import SheetsRepository


class ReferralService:

    def __init__(self, repo: SheetsRepository) -> None:
        self._repo = repo

    def get_referral_count(self, discord_id: str) -> int:
        # Получить количество рефералов для пользователя
        pass

    def get_referral_level(self, count: int) -> int:
        # Получить уровень рефералов на основе количества
        pass

    def get_next_level_progress(self, count: int) -> tuple[int, int]:
        # Получить прогресс к следующему уровню (текущий, необходимый)  
        pass
