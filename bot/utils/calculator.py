import re


def safe_calc(expression: str) -> float:
    if not expression or not expression.strip():
        raise ValueError("Сумма не может быть пустой")
    # Keep only digits, decimal separators, math operators, and k/к suffix
    cleaned = re.sub(r'[^\d.,+*/\skк-]', '', expression)
    cleaned = re.sub(r'\s', '', cleaned)
    cleaned = cleaned.replace(",", ".")
    cleaned = re.sub(r'(\d+)[kк]', r'\1*1000', cleaned, flags=re.IGNORECASE)
    if not cleaned:
        raise ValueError("Сумма не может быть пустой")
    import math
    try:
        import simpleeval
        result = simpleeval.simple_eval(
            cleaned,
            functions={"int": int, "float": float, "abs": abs, "round": round},
            names={},
        )
    except Exception:
        result = float(cleaned)
    if not isinstance(result, (int, float)):
        raise ValueError("Результат не является числом")
    value = float(result)
    if not math.isfinite(value):
        raise ValueError("Сумма должна быть конечным числом")
    return value
