"""The audit log row DTO (see PLAN.md §5.4)."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class AuditActor(StrEnum):
    """Who performed the action being logged (заявка 13.09.2026 п.1).

    The audit embed was written for `USER` only — "🧾 Использование
    команды", with the player in a `Пользователь` field and a command name
    next to it. Automatic role grants went through that same format, which
    read as if the player had run `/progression.sync` themselves. `BOT`
    renders the same event as an action the bot took *on* that player
    instead.
    """

    USER = "user"
    """A person ran a slash command."""

    BOT = "bot"
    """The bot acted on its own (role sync, a scheduled job)."""


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """One row of the `🧾 Использование команды` audit embed."""

    user_id: int
    user_display: str
    channel_display: str
    command: str
    arguments: str
    result: str
    duration_seconds: float
    trace_id: str
    occurred_at: datetime
    actor: AuditActor = AuditActor.USER
    """`BOT` for an action nobody invoked — see `AuditActor`. Defaults to
    `USER`, so every command-tracing call site stays as it was."""
