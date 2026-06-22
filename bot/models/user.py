from dataclasses import dataclass
from typing import Optional


@dataclass
class User:
    discord_id: str
    nickname: str
    coins: float = 0.0
    xp: float = 0.0
    level: int = 0
    referral_code: Optional[str] = None
    referred_by: Optional[str] = None
    referral_count: int = 0
