def parse_ruble_amount(val) -> float:
    """Распарсить сумму из ячейки логов/аналитики (строка с ₽/запятой или число)."""
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace(" ", "").replace(",", ".").replace("₽", "")
    try:
        return float(s)
    except ValueError:
        return 0.0
