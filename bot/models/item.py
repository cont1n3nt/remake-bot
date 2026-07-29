from dataclasses import dataclass
from typing import Optional


@dataclass
class Item:
    id: int
    name: str
    category: str
    price_buy: Optional[float] = None
    price_sell: Optional[float] = None
    emoji: str = ""
    updated_at: str = ""
