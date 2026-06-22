from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    discord_token: str = Field(default="", validation_alias="DISCORD_TOKEN")
    google_sheets_creds: str = Field(default="credentials.json", validation_alias="GOOGLE_SHEETS_CREDS")
    google_sheets_url: str = Field(default="", validation_alias="GOOGLE_SHEETS_URL")
    guild_id: int = Field(default=0, validation_alias="GUILD_ID")
    sheet_name: str = Field(default="Лист1", validation_alias="SHEET_NAME")
    admin_role_name: str = "Admin"

    @property
    def is_valid(self) -> bool:
        return bool(self.discord_token and self.google_sheets_url)


settings = Settings()
