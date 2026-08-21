"""`Player` — a row of the `players` table (sqlite_migration.md §IV.1, Э3).

Replaces the sheet-era `UserProfile`'s `sheet_row`-as-identity: a player is
a first-class row with a surrogate `id`, existing independently of whether
they've ever made a deal (§XIV.1: "строки-заготовки... в SQLite хак
исчезает — players самостоятельная таблица").
"""

from dataclasses import dataclass
from datetime import datetime

from stalbot.domain.nick import NormalizedNick


@dataclass(frozen=True, slots=True)
class Player:
    """One player, identified by a surrogate id, not their sheet row."""

    id: int | None
    """`None` for a not-yet-persisted player — the repository assigns one on insert."""
    nick_norm: NormalizedNick
    nick_display: str
    discord_id: int | None
    referrer_player_id: int | None
    """The resolved referrer (§III.2 variant B: first non-blank `H` among
    the player's own deals) — one field, read consistently everywhere,
    unlike the sheet's `VLOOKUP`/`COUNTIF` pair that disagreed with itself."""
    is_booster: bool
    created_at: datetime
    updated_at: datetime
