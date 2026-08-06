"""Domain exception hierarchy (see PLAN.md §12).

Every exception raised by stalbot code is rooted in `StalbotError`, so the
global handler in `presentation/errors.py` can catch everything expected with
a single `except StalbotError` and map it to a user-facing embed. Exceptions
that are *not* `StalbotError` are unexpected bugs and propagate with a full
traceback into the log.
"""


class StalbotError(Exception):
    """Base class for every exception raised by stalbot code."""


class DomainError(StalbotError):
    """Business-rule violation detected by pure domain code (zero I/O)."""


class AmountParseError(DomainError):
    """A user-supplied money string could not be parsed unambiguously."""


class DeadlineParseError(DomainError):
    """A user-supplied deadline string could not be parsed."""


class NickNotBoundError(DomainError):
    """An operation requires a nick bound to a Discord account, but it is not."""


class PlayerNotFoundError(DomainError):
    """A lookup by game nick found no matching player in the database."""


class ProfileAccessDeniedError(DomainError):
    """A non-admin requester tried to view a profile that is not their own."""


class NoTransactionsYetError(DomainError):
    """`/set_referral` was called for a player with no recorded deals yet.

    The referrer is written to a player's *first* `Тикеты` row (PLAN.md
    §10.12), so there must be at least one before a referrer can be set.
    """


class ItemNotFoundError(DomainError):
    """A catalog lookup for an item failed."""


class DuplicateItemError(DomainError):
    """An item with the same name and category already exists in the catalog."""


class InvalidPeriodError(DomainError):
    """A requested date/period range is invalid (e.g. end before start)."""


class TicketSessionNotFoundError(DomainError):
    """A ticket-flow interaction fired for a channel with no tracked session.

    Should only happen for a stray/stale component (e.g. the channel was
    deleted and its `ticket_sessions` row cleaned up, but an old message's
    button is still clickable) — normal operation always finds a session
    (PLAN.md §11.2 creates one on `on_guild_channel_create`).
    """


class InfrastructureError(StalbotError):
    """Failure talking to an external system (Sheets, cache, Discord)."""


class SheetsUnavailableError(InfrastructureError):
    """The Google Sheets API stayed unreachable after all retries."""


class SheetsWriteConflictError(InfrastructureError):
    """A read-back verification after a write did not match what was sent."""


class CacheStaleError(InfrastructureError):
    """Cached data is older than the configured staleness threshold."""


class ProtectedRangeWriteError(InfrastructureError):
    """Code attempted to write a formula-owned Sheets column.

    Raised by `infrastructure.sheets.protection` before any network call is
    made, so a bug can never overwrite a formula (see PLAN.md §7.3).
    """


class SheetStructureError(InfrastructureError):
    """The spreadsheet's sheets/headers do not match what the bot expects.

    Raised at startup and on each full sync (PLAN.md §16: "Ручные правки
    таблицы ломают структуру" — the bot refuses to run against a sheet whose
    layout it can no longer trust).
    """
