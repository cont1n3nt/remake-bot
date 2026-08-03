"""`Promotion` — one rank/referral-role advancement detected by a progression sync."""

from dataclasses import dataclass
from typing import Literal

from stalbot.domain.nick import NormalizedNick

#: Which ladder advanced.
PromotionAxis = Literal["rank", "referral_role"]


@dataclass(frozen=True, slots=True)
class Promotion:
    """A single player's advancement on one ladder (PLAN.md §9.2)."""

    nick: NormalizedNick
    discord_id: int
    axis: PromotionAxis
    label: str
    perks: tuple[str, ...]
    coins: int
    xp: int
