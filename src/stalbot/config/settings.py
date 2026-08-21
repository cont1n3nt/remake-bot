"""Application settings loaded from `.env` (see `.env.example`).

Required fields are validated immediately by `pydantic-settings` when
`Settings()` is constructed — a missing required variable stops the bot
before it connects to Discord (fail-fast, see PLAN.md §14).
"""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

type OcrEngine = Literal["null", "tesseract", "paddle", "vision"]
type LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Full bot configuration assembled from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="forbid",
    )

    # --- Discord ---
    discord_token: SecretStr
    guild_id: int
    log_channel_id: int
    reviews_channel_id: int

    # --- Cache and background tasks ---
    cache_db_path: Path = Path("./data/cache.sqlite3")
    progression_poll_seconds: int = Field(default=300, gt=0)

    # --- Behavior toggles (PLAN.md §17.2) ---
    price_import_confirm: bool = True
    admin_can_view_any_profile: bool = True

    # --- OCR (groundwork for M13; v1.0 runs on NullOcrGateway) ---
    ocr_enabled: bool = False
    ocr_engine: OcrEngine = "null"
    ocr_keep_samples: bool = True
    ocr_samples_dir: Path = Path("./data/ocr_samples")
    ocr_match_threshold: int = Field(default=85, ge=0, le=100)
    ocr_timeout_seconds: int = Field(default=20, gt=0)

    log_level: LogLevel = "INFO"
