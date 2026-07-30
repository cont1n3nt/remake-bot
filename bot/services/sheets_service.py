from typing import Optional

from bot.config.constants import DATA_START_ROW
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
            turnover=data.get("turnover", 0.0),
        )

    def ensure_user(self, nickname: str) -> bool:
        return self._repo.ensure_user(nickname)

    def set_referred_by(self, nickname: str, referrer: str) -> None:
        self._repo.set_referred_by(nickname.lower().strip(), referrer.lower().strip())

    def user_has_referral(self, nickname: str) -> bool:
        return self._repo.user_has_referral(nickname)

    def save_transaction(
        self, nickname: str, tx_type: str, amount: float,
        referrer: str | None = None,
    ) -> Transaction:
        nickname = nickname.lower().strip()
        if referrer:
            referrer = referrer.lower().strip()
        self._repo.append_transaction(nickname, tx_type, amount, referrer)
        return Transaction(nickname=nickname, tx_type=tx_type, amount=amount)

    # ------------------------------------------------------------------ #
    #  Проксирующие методы для items.py / admin_cmds.py / analytics.py    #
    #  (REFACTORING_PLAN.md, Этап E.1) — дословно делегируют в repo, чтобы#
    #  эти коги перестали обращаться к bot.repo напрямую (см. Фаза E).    #
    # ------------------------------------------------------------------ #

    def get_all_items(self) -> list[dict]:
        return self._repo.get_all_items()

    def find_item_by_name_and_category(self, name: str, category: str) -> Optional[dict]:
        return self._repo.find_item_by_name_and_category(name, category)

    def upsert_item(
        self, name: str, category: str,
        price_buy: Optional[float] = None,
        price_sell: Optional[float] = None,
        emoji: str = "",
    ) -> dict:
        return self._repo.upsert_item(
            name, category, price_buy=price_buy, price_sell=price_sell, emoji=emoji,
        )

    def delete_item(self, name: str, category: Optional[str] = None) -> bool:
        return self._repo.delete_item(name, category)

    def set_discord_id(self, unique_nick: str, discord_id: str) -> bool:
        return self._repo.set_discord_id(unique_nick, discord_id)

    def get_transactions(self, start_row: int = DATA_START_ROW, end_row: int = 2000) -> list[list]:
        return self._repo.get_transactions(start_row, end_row)

    def sync_prices_to_sheets(self) -> dict:
        return self._repo.sync_prices_to_sheets()
