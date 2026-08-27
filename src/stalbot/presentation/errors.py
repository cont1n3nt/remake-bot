"""Global slash-command error handler (PLAN.md §12).

One place maps the domain/infrastructure exception hierarchy (plus
discord.py's own `app_commands` errors, notably a failed `@admin_only()`
check) to a single `error`-style embed, and logs anything unrecognized under
the trace id shown to the user.
"""

import logging
import math

import discord
from discord import app_commands

from stalbot.domain.errors import (
    AmountParseError,
    CouponAlreadyRedeemedError,
    CouponInactiveError,
    CouponNotFoundError,
    CouponWrongKindError,
    DeadlineParseError,
    DealNotFoundError,
    DomainError,
    DuplicateItemError,
    InvalidCategoryPriceError,
    InvalidPeriodError,
    ItemNotFoundError,
    NickNotBoundError,
    NoTransactionsYetError,
    PlayerNotFoundError,
    ProfileAccessDeniedError,
    StalbotError,
    TicketSessionNotFoundError,
)
from stalbot.infrastructure.logging.trace import current_trace_id
from stalbot.presentation.embeds.factory import EmbedFactory

logger = logging.getLogger(__name__)

#: User-facing text per domain exception type. A `DomainError` not listed
#: here falls back to `str(exc)` (curated to be user-facing by convention);
#: anything else unmapped gets a generic message — see `_resolve_message`.
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
    DealNotFoundError: "Сделка с таким ID не найдена.",
    DuplicateItemError: "Такой предмет уже есть в базе.",
    InvalidCategoryPriceError: (
        "У ресурса указывается только цена покупки, у буста — только цена продажи."
    ),
    InvalidPeriodError: "Некорректный период.",
    TicketSessionNotFoundError: (
        "Тикет не найден или ещё не инициализирован. Обратитесь к администратору."
    ),
    CouponNotFoundError: "Купон с таким кодом не найден.",
    CouponInactiveError: "Купон отключён, просрочен или исчерпан.",
    CouponAlreadyRedeemedError: "Вы уже использовали этот купон раньше — повторно нельзя.",
    CouponWrongKindError: (
        "Этот купон не подходит для этого типа заявки "
        "(скидка — только заказ бустов, наценка — только продажа/скупка)."
    ),
}

_PERMISSION_DENIED_MESSAGE = "Недостаточно прав для этого действия."


def _resolve_cause_message(cause: BaseException | None, trace_id: str) -> str:
    """Map an already-unwrapped exception to the text shown in the error embed.

    Shared by `on_app_command_error` (which unwraps `CommandInvokeError`
    first — its `__cause__` is typed as optional, though discord.py always
    sets it) and `on_modal_error` (SEC-4) — a `discord.ui.Modal.on_error`
    already receives the raw `on_submit` exception, never wrapped the way
    `app_commands` wraps a slash-command's.
    """
    if isinstance(cause, StalbotError):
        for exc_type, message in _DOMAIN_MESSAGES.items():
            if isinstance(cause, exc_type):
                return message
        if isinstance(cause, DomainError):
            # Domain messages are curated to be user-facing by convention —
            # safe to surface directly even when not explicitly mapped above.
            return str(cause) or "Произошла ошибка."
        # Anything else (InfrastructureError and any future StalbotError not
        # rooted in DomainError) may carry internal details — SQL, table/column
        # names — that must never reach Discord. Log it under the trace id
        # shown to the user instead of leaking `str(cause)`.
        logger.warning("infrastructure error (trace %s): %s", trace_id, cause, exc_info=cause)
        return f"Внутренняя ошибка, обратитесь к администратору. Trace: `{trace_id}`"

    logger.error("unhandled error (trace %s)", trace_id, exc_info=cause)
    return f"Внутренняя ошибка, обратитесь к администратору. Trace: `{trace_id}`"


def _resolve_message(error: app_commands.AppCommandError, trace_id: str) -> str:
    """Map *error* to the text shown in the error embed."""
    if isinstance(error, app_commands.CommandOnCooldown):
        # SEC-5: `CommandOnCooldown` is itself a `CheckFailure` subclass —
        # checked first so a cooldown hit says so, instead of the generic
        # (and here actively misleading) "insufficient permissions".
        # ceil, not round: rounding a sub-second retry_after down to "0 с."
        # would tell an admin to retry immediately while still rate-limited.
        return f"Слишком часто. Попробуйте снова через {math.ceil(error.retry_after)} с."
    if isinstance(error, app_commands.CheckFailure):
        return _PERMISSION_DENIED_MESSAGE

    cause = error.__cause__ if isinstance(error, app_commands.CommandInvokeError) else error
    return _resolve_cause_message(cause, trace_id)


async def _send_error_embed(interaction: discord.Interaction, embed: discord.Embed) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=True)


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
    await _send_error_embed(interaction, embed)


async def on_modal_error(
    interaction: discord.Interaction,
    error: Exception,
    *,
    embeds: EmbedFactory,
) -> None:
    """Handle every `discord.ui.Modal.on_submit` error the same way (SEC-4).

    A Modal's `on_error` is a separate discord.py dispatch path from
    `app_commands`' — without this, discord.py's default `Modal.on_error`
    just logs and returns, so the player/admin sees the "thinking…"
    indicator hang with no explanation and no trace id.

    Args:
        interaction: The interaction that submitted the failing modal.
        error: The exception raised by `on_submit`.
        embeds: Factory used to build the error embed shown to the user.
    """
    trace_id = current_trace_id()
    message = _resolve_cause_message(error, trace_id)
    embed = embeds.error("Ошибка", message)
    await _send_error_embed(interaction, embed)
