from typing import Optional

from bot.models.user import User
from bot.models.transaction import Transaction
from bot.repositories.sheets_repository import SheetsRepository


class SheetsService:

    def __init__(self, repo: SheetsRepository) -> None:
        self._repo = repo

    def get_user(self, nickname: str) -> Optional[User]:
        data = self._repo.find_user(nickname)
        if data is None:
            return None

        return User(
            nickname=data["nickname"],
            coins=data["coins"],
            xp=data["xp"],
            rank=data["rank"],
            referral_count=data["referral_count"],
            referral_role=data["referral_role"],
            booster=data["booster"],
            referred_by=data.get("referred_by"),
        )

    def ensure_user(self, nickname: str) -> bool:
        return self._repo.ensure_user(nickname)

    def set_referred_by(self, nickname: str, referrer: str) -> None:
        self._repo.set_referred_by(nickname, referrer)

    def user_has_referral(self, nickname: str) -> bool:
        return self._repo.user_has_referral(nickname)

    def save_transaction(
        self, nickname: str, tx_type: str, amount: float,
        referrer: str | None = None,
    ) -> Transaction:
        self._repo.append_transaction(nickname, tx_type, amount, referrer)
        return Transaction(nickname=nickname, tx_type=tx_type, amount=amount)
