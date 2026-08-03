"""`ProgressionState` — cache-only bookkeeping for promotion detection (PLAN.md §8.1).

Has no Sheets counterpart: it exists purely so `ProgressionService` (M3)
never re-announces a promotion it already congratulated.
"""

from dataclasses import dataclass
from datetime import datetime

from stalbot.domain.nick import NormalizedNick


@dataclass(frozen=True, slots=True)
class ProgressionState:
    """The last rank/referral role announced for a player."""

    nick: NormalizedNick
    last_rank: str | None
    last_referral_role: str | None
    announced_at: datetime | None
    #: Set by `/set_rank` (M8); while true, the background poller leaves the
    #: rank ladder alone entirely — it neither grants the sheet-computed
    #: rank role nor revokes the manually-assigned one (PLAN.md §10.12).
    manual_rank_role: bool = False
