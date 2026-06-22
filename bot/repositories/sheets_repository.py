from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

from bot.config.constants import (
    SHEET_USERS,
    SHEET_TRANSACTIONS,
    MAX_RETRIES,
)
from bot.models.transaction import Transaction


class SheetsRepository:

    def __init__(self, creds_path: str, sheet_url: str) -> None:
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
        self._client = gspread.authorize(creds)
        self._spreadsheet = self._client.open_by_url(sheet_url)
        self._users: Optional[gspread.Worksheet] = None
        self._transactions: Optional[gspread.Worksheet] = None

    def _get_users(self) -> gspread.Worksheet:
        if self._users is None:
            self._users = self._spreadsheet.worksheet(SHEET_USERS)
        return self._users

    def _get_transactions(self) -> gspread.Worksheet:
        if self._transactions is None:
            self._transactions = self._spreadsheet.worksheet(SHEET_TRANSACTIONS)
        return self._transactions

    def find_user(self, discord_id: str) -> Optional[list[str]]:
        """Search user by discord_id. Returns row or None."""
        pass

    def create_user(self, discord_id: str, nickname: str) -> None:
        """Append new user row."""
        pass

    def update_user(self, discord_id: str, **kwargs) -> None:
        """Update user cells by column name."""
        pass

    def find_by_referral_code(self, code: str) -> Optional[list[str]]:
        """Find user by referral code. Returns row or None."""
        pass

    def append_transaction(self, transaction: Transaction) -> None:
        """Append new transaction row."""
        pass

    def get_all_users(self) -> list[list[str]]:
        """Return all user rows."""
        pass
