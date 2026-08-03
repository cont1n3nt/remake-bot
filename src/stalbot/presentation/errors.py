"""Global slash-command error handler (PLAN.md §12).

One place maps the domain/infrastructure exception hierarchy (plus
discord.py's own `app_commands` errors, notably a failed `@admin_only()`
check) to a single `error`-style embed, and logs anything unrecognized under
the trace id shown to the user.
"""

import logging

import discord
from discord import app_commands

from stalbot.domain.errors import (
    AmountParseError,
    CacheStaleError,
    DeadlineParseError,
    DuplicateItemError,
    InvalidPeriodError,
    ItemNotFoundError,
    NickNotBoundError,
    NoTransactionsYetError,
    PlayerNotFoundError,
    ProfileAccessDeniedError,
    SheetsUnavailableError,
    SheetsWriteConflictError,
    StalbotError,
    TicketSessionNotFoundError,
)
from stalbot.infrastructure.logging.trace import current_trace_id
from stalbot.presentation.embeds.factory import EmbedFactory

logger = logging.getLogger(__name__)

#: User-facing text per domain exception type. Anything not listed falls
#: back to `str(exc)` if it is non-empty, else a generic message.
_DOMAIN_MESSAGES: dict[type[StalbotError], str] = {
    AmountParseError: (
        "Не удалось распознать сумму. Примеры корректного ввода: "
        "`299900`, `299 900 ₽`, `1.5кк`, `250к`, `299 900 + 10000`."
    ),
    DeadlineParseError: "Не удалось распознать дату.",
    NickNotBoundError: "Этот игровой ник не привязан к Discord-аккаунту.",
    NoTransactionsYetError: "Реферала можно указать только после первой сделки игрока.",
    PlayerNotFoundError: "Игрок с таким ником не найден в базе.",
    ProfileAccessDeniedError: "Вы можете смотреть только свой профиль.",
    ItemNotFoundError: "Предмет не найден в базе.",
    DuplicateItemError: "Такой предмет уже есть в базе.",
    InvalidPeriodError: "Некорректный период.",
    TicketSessionNotFoundError: (
        "Тикет не найден или ещё не инициализирован. Обратитесь к администратору."
    ),
    SheetsUnavailableError: "Google Таблица временно недоступна, попробуйте позже.",
    SheetsWriteConflictError: "Не удалось подтвердить запись, попробуйте ещё раз.",
    CacheStaleError: "Данные устарели, попробуйте ещё раз через несколько секунд.",
}

_PERMISSION_DENIED_MESSAGE = "Недостаточно прав для этого действия."


def _resolve_message(error: app_commands.AppCommandError, trace_id: str) -> str:
    """Map *error* to the text shown in the error embed."""
    if isinstance(error, app_commands.CheckFailure):
        return _PERMISSION_DENIED_MESSAGE

    cause = error.__cause__ if isinstance(error, app_commands.CommandInvokeError) else error
    if isinstance(cause, StalbotError):
        for exc_type, message in _DOMAIN_MESSAGES.items():
            if isinstance(cause, exc_type):
                return message
        return str(cause) or "Произошла ошибка."

    logger.exception("unhandled app command error (trace=%s)", trace_id, exc_info=error)
    return f"Внутренняя ошибка, обратитесь к администратору. Trace: `{trace_id}`"


async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
    *,
    embeds: EmbedFactory,
) -> None:
    """Handle every slash-command error the same way.

    Args:
        interaction: The interaction that failed.
        error: The error raised by discord.py.
        embeds: Factory used to build the error embed shown to the user.
    """
    trace_id = current_trace_id()
    message = _resolve_message(error, trace_id)
    embed = embeds.error("Ошибка", message)

    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=True)
