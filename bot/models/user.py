from dataclasses import dataclass


@dataclass
class User:
    nickname: str
    coins: float = 0.0
    xp: float = 0.0
    rank: str = ""
    referral_count: int = 0
    referral_role: str = ""
    referred_by: str | None = None
    booster: bool = False
    turnover: float = 0.0
