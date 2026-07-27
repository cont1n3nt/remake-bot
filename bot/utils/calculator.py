import re
from typing import Union


def safe_calc(expression: str) -> float:
    cleaned = expression.replace(" ", "")
    cleaned = cleaned.replace(",", ".")
    import simpleeval
    result = simpleeval.simple_eval(
        cleaned,
        functions={"int": int, "float": float, "abs": abs, "round": round},
        names={},
    )
    if not isinstance(result, (int, float)):
        raise ValueError("Результат не является числом")
    return float(result)
