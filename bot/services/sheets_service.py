from typing import Optional

from bot.models.user import User
from bot.models.transaction import Transaction
from bot.repositories.sheets_repository import SheetsRepository


class SheetsService:

    def __init__(self, repo: SheetsRepository) -> None:
        self._repo = repo

    def get_user(self, discord_id: str) -> Optional[User]:
        """Fetch user from sheets and map to User model."""
        pass

    def create_user(self, discord_id: str, nickname: str) -> User:
        """Create new user row and return User model."""
        pass

    def save_transaction(
        self,
        discord_id: str,
        nickname: str,
        tx_type: str,
        amount: float,
        raw_log: str,
    ) -> Transaction:
        """Build Transaction, append to sheets, return it."""
        pass

    def find_by_referral_code(self, code: str) -> Optional[User]:
        """Find user by referral code."""
        pass

    def ensure_user(self, discord_id: str, nickname: str) -> User:
        """Return existing user or create new one."""
        pass
