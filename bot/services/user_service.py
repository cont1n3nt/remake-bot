from typing import Optional

from bot.models.user import User
from bot.services.sheets_service import SheetsService


class UserService:

    def __init__(self, sheets: SheetsService) -> None:
        self._sheets = sheets

    def get_profile(self, nickname: str) -> Optional[User]:
        return self._sheets.get_user(nickname)

    def set_referral(self, nickname: str, referrer: str) -> User:
        if nickname.lower() == referrer.lower():
            raise ValueError("Нельзя указать самого себя")

        user = self._sheets.get_user(nickname)
        if user is None:
            self._sheets.ensure_user(nickname)
        elif user.referred_by:
            raise ValueError("Реферал уже указан, изменить нельзя")

        self._sheets.set_referred_by(nickname, referrer)
        return self._sheets.get_user(nickname) or User(nickname=nickname)

    def get_referral_info(self, nickname: str) -> Optional[User]:
        return self._sheets.get_user(nickname)
