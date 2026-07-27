import re


def safe_calc(expression: str) -> float:
    # Remove all currency symbols and non-math characters
    cleaned = re.sub(r'[₽$€¥£₸₴฿₩₪₫₭₮₰₱₲₳₵₶₷₸₹₺₻₼₽₾₿\s]', '', expression)
    cleaned = cleaned.replace(",", ".")
    # Handle "k" / "к" suffix (thousands)
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
