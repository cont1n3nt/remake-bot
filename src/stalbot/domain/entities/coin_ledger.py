"""`CoinLedgerEntry` — a row of the `coin_ledger` table.

sqlite_migration.md §IV.1, Э3. Signed: negative is a spend (Магазин,
Э12+), positive a grant. Empty until Э12's shop exists; declared now so
`ProgressionRepository`'s aggregation query already reads it without a
later schema change.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class CoinLedgerEntry:
    """One signed Coins adjustment for a player."""

    id: int | None
    """`None` for a not-yet-persisted entry — the repository assigns one on insert."""
    player_id: int
    delta: int
    """Never zero — `coin_ledger.delta CHECK (delta <> 0)`."""
    reason: str
    created_by: int | None
    """Discord id of who caused this entry, or `None` for a system-generated one."""
    created_at: datetime
