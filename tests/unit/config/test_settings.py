"""Tests for `stalbot.config.settings` (PLAN.md §14: fail-fast `.env` validation)."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from stalbot.config.settings import Settings

_REQUIRED_ENV: dict[str, str] = {
    "DISCORD_TOKEN": "fake-token",
    "GUILD_ID": "1475147129201627208",
    "LOG_CHANNEL_ID": "1518330495505797143",
    "REVIEWS_CHANNEL_ID": "1490342809075716237",
    "SPREADSHEET_ID": "1W3HDdzvnQ4Uzyn86RQUUp-hrzFgBikowtP5LBoq_Ov0",
}


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear every stalbot env var so tests never see the developer's real `.env`."""
    for key in (
        *_REQUIRED_ENV,
        "CACHE_DB_PATH",
        "OCR_ENABLED",
        "LOG_LEVEL",
        "SYNC_USERS_INTERVAL_SECONDS",
        "SYNC_ITEMS_INTERVAL_SECONDS",
        "PROGRESSION_POLL_SECONDS",
    ):
        monkeypatch.delenv(key, raising=False)


def _set_required_env(monkeypatch: pytest.MonkeyPatch, *, skip: str | None = None) -> None:
    for key, value in _REQUIRED_ENV.items():
        if key != skip:
            monkeypatch.setenv(key, value)


def test_loads_with_all_required_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_env(monkeypatch)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.discord_token.get_secret_value() == "fake-token"
    assert settings.guild_id == 1475147129201627208
    assert settings.spreadsheet_id == "1W3HDdzvnQ4Uzyn86RQUUp-hrzFgBikowtP5LBoq_Ov0"


@pytest.mark.parametrize("missing", sorted(_REQUIRED_ENV))
def test_missing_required_field_fails_fast(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    _set_required_env(monkeypatch, skip=missing)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_defaults_are_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_env(monkeypatch)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.cache_db_path == Path("./data/cache.sqlite3")
    assert settings.ocr_enabled is False
    assert settings.ocr_engine == "null"
    assert settings.log_level == "INFO"


def test_unrelated_env_vars_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """`extra="forbid"` guards explicit kwargs; stray env vars are simply unused."""
    _set_required_env(monkeypatch)
    monkeypatch.setenv("TOTALLY_UNRELATED_ENV_VAR", "x")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert not hasattr(settings, "totally_unrelated_env_var")


def test_explicit_unknown_kwarg_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_env(monkeypatch)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, totally_unknown_field="x")  # type: ignore[call-arg]


def test_invalid_log_level_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    """INFRA2-6: a typo'd `LOG_LEVEL` must be caught at startup, not surface
    later as a less-legible error from `logging.setLevel`."""
    _set_required_env(monkeypatch)
    monkeypatch.setenv("LOG_LEVEL", "VERBOSE")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "field",
    ["SYNC_USERS_INTERVAL_SECONDS", "SYNC_ITEMS_INTERVAL_SECONDS", "PROGRESSION_POLL_SECONDS"],
)
@pytest.mark.parametrize("value", ["0", "-1"])
def test_non_positive_sync_interval_fails_fast(
    monkeypatch: pytest.MonkeyPatch, field: str, value: str
) -> None:
    """INFRA2-7: `0`/negative would drive the matching `tasks.loop` into a
    hot loop hammering Discord/Sheets — must be rejected at startup, not
    discovered from a rate-limit storm in production."""
    _set_required_env(monkeypatch)
    monkeypatch.setenv(field, value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]
