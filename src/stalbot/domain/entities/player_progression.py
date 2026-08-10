"""`PlayerProgressionRecord` — a row of the `player_progression` table.

sqlite_migration.md §IV.1, §III.3 "Прогрессия материализуется", Э3. The
persisted form of `domain.progression.calculator.PlayerProgression`:
same numbers, but `rank`/`referral_role` are stored as their ladder-tier
*keys* (e.g. `"prestige"`), not the sheet's emoji label (§III.3), and the
full `ProgressionBreakdown` is serialized to `breakdown_json` for
audit/debugging rather than exploded into columns.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class PlayerProgressionRecord:
    """One player's materialized progression snapshot."""

    player_id: int
    purchase_turnover: int
    sale_turnover: int
    total_turnover: int
    referral_count: int
    coins: int
    xp: int
    rank_key: str | None
    referral_role_key: str | None
    breakdown_json: str
    calculator_version: int
    computed_at: datetime
