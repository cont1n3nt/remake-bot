"""`UserProfile` — a row of the `Общая база пользователей` block (PLAN.md §6.1, §6.4)."""

from dataclasses import dataclass
from decimal import Decimal

from stalbot.domain.nick import NormalizedNick


@dataclass(frozen=True, slots=True)
class UserProfile:
    """A player's progression state, as computed by the sheet's formulas.

    `rank` and `referral_role` are the raw label strings from columns `R`/`S`
    (e.g. `"💎 Elite"`), exactly as decision A2 requires: the bot never
    computes a rank itself, it only reads the sheet's own answer. Mapping a
    label to its perks/thresholds is `domain.progression.RankLadder`'s job
    (M3).
    """

    row: int
    nick: NormalizedNick
    discord_id: int | None
    coins: int
    xp: int
    buy_turnover: Decimal
    sell_turnover: Decimal
    total_turnover: Decimal
    referrals_count: int
    is_booster: bool
    rank: str | None
    referral_role: str | None
