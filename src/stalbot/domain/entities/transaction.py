"""`TransactionRecord` — a row of the `Тикеты` block (see PLAN.md §6.1, §6.4)."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from stalbot.domain.enums import DealType
from stalbot.domain.nick import NormalizedNick


@dataclass(frozen=True, slots=True)
class TransactionRecord:
    """A single recorded deal.

    `coins`/`xp` mirror the sheet's formula columns `F`/`G` and are only ever
    read back after the sheet recalculates them — the bot never computes
    them itself (decision A2).
    """

    row: int
    at: datetime
    nick: NormalizedNick
    nick_display: str
    deal_type: DealType
    amount: Decimal
    coins: int
    xp: int
    referrer: NormalizedNick | None
