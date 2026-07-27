import re


def safe_calc(expression: str) -> float:
    # Keep only digits, decimal separators, math operators, and k/к suffix
    cleaned = re.sub(r'[^\d.,+*/\skк-]', '', expression)
    cleaned = re.sub(r'\s', '', cleaned)
    cleaned = cleaned.replace(",", ".")
    cleaned = re.sub(r'(\d+)[kк]', r'\1*1000', cleaned, flags=re.IGNORECASE)
    import simpleeval
    result = simpleeval.simple_eval(
        cleaned,
        functions={"int": int, "float": float, "abs": abs, "round": round},
        names={},
    )
    if not isinstance(result, (int, float)):
        raise ValueError("Результат не является числом")
    return float(result)
