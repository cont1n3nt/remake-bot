"""`Deal` — a row of the `deals` table (sqlite_migration.md §IV.1, Э3).

Replaces the sheet-era `TransactionRecord`: keyed by `player_id`, not a
nick, and carries `rank_at_deal`/`booster_at_deal` snapshots so a rank's
bonuses apply going forward from the moment it was reached, not
retroactively across a player's whole history the way the sheet's formula
effectively did (§XIV.1).
"""

from dataclasses import dataclass
from datetime import datetime

from stalbot.domain.enums import DealSource, DealType, OccurredAtKind
from stalbot.domain.money import Rub


@dataclass(frozen=True, slots=True)
class Deal:
    """One recorded purchase or sale."""

    id: int | None
    """`None` for a not-yet-persisted deal — the repository assigns one on insert."""
    player_id: int
    occurred_at: datetime
    occurred_at_kind: OccurredAtKind
    deal_type: DealType
    amount: Rub
    coins: int
    xp: int
    rank_at_deal: str | None
    """The player's `rank_key` at the moment of this deal, or `None`."""
    booster_at_deal: bool
    recorded_by: int | None
    """Discord id of who recorded this deal (admin for `/add`, ticket confirmer)."""
    source: DealSource
    legacy_sheet_row: int | None
    """The Тикеты row this deal was imported from (Э4), or `None` for a
    deal recorded natively by the bot."""
    created_at: datetime
