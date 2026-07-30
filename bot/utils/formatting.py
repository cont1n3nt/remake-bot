"""Единственный форматтер чисел проекта.

До этого в коде жили четыре независимые реализации с разным поведением
(`ocr_service._fmt`, `admin_cmds._fmt`, `analytics._fmt`, `embeds._fmt_thousands`,
`transactions._amount_str`) — числа в /profile выглядели иначе, чем в /logs и
/day. Теперь все они — тонкие обёртки над `format_amount`.

Правила:
  * разделитель тысяч — пробел: 1000000 → "1 000 000";
  * дробная часть округляется до двух знаков, хвостовые нули убираются;
  * NaN/inf не роняют вызывающий код (раньше падали на `int(n)`).
"""

import math

__all__ = ["format_amount"]

_FRACTION_DIGITS = 2


def _group_thousands(int_digits: str) -> str:
    groups = []
    while int_digits:
        groups.append(int_digits[-3:])
        int_digits = int_digits[:-3]
    return " ".join(reversed(groups))


def format_amount(n: float | int) -> str:
    """Отформатировать число для вывода пользователю.

    >>> format_amount(1000000)
    '1 000 000'
    >>> format_amount(1500000.567)
    '1 500 000.57'
    >>> format_amount(-5.0)
    '-5'
    """
    if isinstance(n, float) and not math.isfinite(n):
        return str(n)

    rounded = round(float(n), _FRACTION_DIGITS)
    negative = rounded < 0
    text = f"{abs(rounded):.{_FRACTION_DIGITS}f}"

    int_part, _, frac_part = text.partition(".")
    frac_part = frac_part.rstrip("0")

    result = _group_thousands(int_part)
    if frac_part:
        result = f"{result}.{frac_part}"
    return f"-{result}" if negative else result
