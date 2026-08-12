"""Request/result DTOs for `TransactionService.register()`.

PLAN.md §10.1, §7.4; sqlite_migration.md Э7.
"""

from dataclasses import dataclass
from decimal import Decimal

from stalbot.domain.entities.deal import Deal
from stalbot.domain.enums import DealSource, DealType


@dataclass(frozen=True, slots=True)
class AddTransactionRequest:
    """Everything needed to record one deal, shared by `/add` and ticket confirmation."""

    nick: str
    """As typed by the admin — original casing, not yet normalized."""
    deal_type: DealType
    amount: Decimal
    discord_id: int
    idempotency_key: str
    referrer_nick: str | None = None
    """As typed by the admin, if given."""
    force_rebind: bool = False
    """Set once the admin has confirmed overwriting an existing Discord binding."""
    source: DealSource = DealSource.ADD
    """What produced this request — `/add` vs. ticket confirmation."""


@dataclass(frozen=True, slots=True)
class TransactionRegistrationResult:
    """What `register()` actually did, for the caller to build a response from."""

    deal: Deal
    nick_display: str
    """Original-case nick, for display — `Deal` itself only carries `player_id`."""
    discord_bound: bool
    """Whether this call bound (or rebound) the nick's Discord id."""
    replayed: bool = False
    """`True` if this call didn't write anything — the idempotency key was
    already recorded by an earlier (or concurrently racing) call with the
    same key, so `deal` is that earlier write, replayed back. Callers
    that trigger their own post-confirm side effects (announcements,
    downstream syncs) should skip them on a replay: the winning call
    already ran them (CLUSTER-1/TICK-1, PLAN.md §7.4)."""
