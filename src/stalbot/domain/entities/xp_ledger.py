"""`XpLedgerEntry` — a row of the `xp_ledger` table.

заявка 13.09.2026 п.2, migration 0012. Signed: positive is a grant,
negative takes XP back (a refunded «Сухой паёк»). Mirrors
`CoinLedgerEntry` so the two adjustment channels read alike.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class XpLedgerEntry:
    """One signed XP adjustment for a player."""

    id: int | None
    """`None` for a not-yet-persisted entry — the repository assigns one on insert."""
    player_id: int
    delta: int
    """Never zero — `xp_ledger.delta CHECK (delta <> 0)`."""
    reason: str
    created_by: int | None
    """Discord id of who caused this entry, or `None` for a system-generated one."""
    created_at: datetime
