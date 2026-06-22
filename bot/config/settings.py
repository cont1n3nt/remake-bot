from dataclasses import dataclass, field

from dotenv import load_dotenv
import os


load_dotenv()


@dataclass
class Settings:
    discord_token: str = field(default_factory=lambda: os.getenv("DISCORD_TOKEN", ""))
    google_sheets_creds: str = field(
        default_factory=lambda: os.getenv("GOOGLE_SHEETS_CREDS", "creds.json")
    )
    google_sheets_url: str = field(
        default_factory=lambda: os.getenv("GOOGLE_SHEETS_URL", "")
    )

    users_sheet_name: str = "Users"
    transactions_sheet_name: str = "Transactions"

    guild_id: int = field(
        default_factory=lambda: int(os.getenv("GUILD_ID", "0"))
    )

    admin_role_name: str = "Admin"

    @property
    def is_valid(self) -> bool:
        return bool(self.discord_token and self.google_sheets_url)


settings = Settings()
