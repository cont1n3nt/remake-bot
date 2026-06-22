from dataclasses import dataclass


@dataclass
class Transaction:
    nickname: str
    tx_type: str
    amount: float
