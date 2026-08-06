"""Request/result DTOs for `TransactionService.register()` (PLAN.md §10.1, §7.4)."""

from dataclasses import dataclass
from decimal import Decimal

from stalbot.domain.entities.transaction import TransactionRecord
from stalbot.domain.enums import DealType


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


@dataclass(frozen=True, slots=True)
class TransactionRegistrationResult:
    """What `register()` actually did, for the caller to build a response from."""

    record: TransactionRecord
    discord_bound: bool
    """Whether this call bound (or rebound) the nick's Discord id."""
    formula_pending: bool
    """`True` if `F`/`G` never resolved even after the `copyPaste` fallback —
    the caller should show `coins`/`xp` as pending rather than as final."""
    replayed: bool = False
    """`True` if this call didn't write anything — the idempotency key was
    already recorded by an earlier (or concurrently racing) call with the
    same key, so `record` is that earlier write, replayed back. Callers
    that trigger their own post-confirm side effects (announcements,
    downstream syncs) should skip them on a replay: the winning call
    already ran them (CLUSTER-1/TICK-1, PLAN.md §7.4)."""
