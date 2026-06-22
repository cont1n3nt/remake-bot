from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Transaction:
    discord_id: str
    nickname: str
    tx_type: str
    amount: float
    raw_log: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
