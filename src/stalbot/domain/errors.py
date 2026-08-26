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


class InvalidCategoryPriceError(DomainError):
    """A resource was given a sell price, or a boost a buy price (sqlite_migration.md §I.5).

    `category` is the trade side, not a taxonomy: a resource is only ever
    bought, a boost only ever sold — `catalog_items`'s own `CHECK` enforces
    the same rule at the storage layer.
    """


class DuplicateItemError(DomainError):
    """An item with the same name and category already exists in the catalog."""


class InvalidPeriodError(DomainError):
    """A requested date/period range is invalid (e.g. end before start)."""


class DealNotFoundError(DomainError):
    """A lookup/delete by deal id found no matching row (`/del_deal`)."""


class TicketSessionNotFoundError(DomainError):
    """A ticket-flow interaction fired for a channel with no tracked session.

    Should only happen for a stray/stale component (e.g. the channel was
    deleted and its `ticket_sessions` row cleaned up, but an old message's
    button is still clickable) — normal operation always finds a session
    (PLAN.md §11.2 creates one on `on_guild_channel_create`).
    """


class CouponNotFoundError(DomainError):
    """No coupon exists with the typed code."""


class CouponInactiveError(DomainError):
    """The coupon exists but was disabled, expired, or has hit its use limit."""


class CouponAlreadyRedeemedError(DomainError):
    """This Discord account has already redeemed this coupon once (заявка 26.08.2026)."""


class InfrastructureError(StalbotError):
    """Failure talking to an external system (cache, Discord)."""


class DatabaseError(InfrastructureError):
    """The SQLite cache failed in a way the caller should surface to the user."""
