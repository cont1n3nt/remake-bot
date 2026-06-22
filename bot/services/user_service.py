from typing import Optional

from bot.models.user import User
from bot.services.sheets_service import SheetsService


class UserService:

    def __init__(self, sheets: SheetsService) -> None:
        self._sheets = sheets

    def get_profile(self, discord_id: str) -> Optional[User]:
        """Get user profile data."""
        pass

    def set_referral_code(self, discord_id: str, code: str) -> User:
        """
        Set referral code for user.

        Raises:
            ValueError: if code already taken or user already has a code.
        """
        pass

    def get_referral_info(self, discord_id: str) -> User:
        """Get referral stats for user."""
        pass
