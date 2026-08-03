"""Single source of truth for parsing, evaluating and formatting money values.

Used by `/add`, ticket confirmation, `/setprice`, `/setboost`, `/new_price`,
boost order calculations and the future `/calc` command. Nowhere else in the
project should a raw `int(...)`/`float(...)` be built from user-supplied
money text (see PLAN.md §5.1).
"""

import ast
import re
import unicodedata
from collections.abc import Iterator
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Final

from stalbot.domain.errors import AmountParseError

# --- shared normalization tables --------------------------------------------

#: Unicode space variants that show up in copy-pasted Discord/Sheets text.
_SPACE_CHARS: Final = "    ⁠"
_SPACE_TRANSLATION: Final = {ord(ch): " " for ch in _SPACE_CHARS}

#: Currency markers stripped from either edge of a number token (case-insensitive).
_CURRENCY_TOKEN: Final = r"(?:₽|\$|€|rub|руб\.?|р\.?|p\.?)"
_CURRENCY_PREFIX_RE: Final = re.compile(rf"^\s*{_CURRENCY_TOKEN}\s*", re.IGNORECASE)
_CURRENCY_SUFFIX_RE: Final = re.compile(rf"\s*{_CURRENCY_TOKEN}\s*$", re.IGNORECASE)

#: A space/underscore between digits is a thousands separator, not part of the
#: value, when it is immediately followed by exactly three more digits.
_THOUSANDS_GLUE_RE: Final = re.compile(r"(?<=\d)[ _](?=\d{3}(?!\d))")

#: Suffix -> multiplier. Longest tokens first so the regex alternation below
#: never matches "к" when the text actually reads "кк"/"ккк".
_MULTIPLIERS: Final[tuple[tuple[str, Decimal], ...]] = (
    ("ккк", Decimal(10) ** 9),
    ("kkk", Decimal(10) ** 9),
    ("кк", Decimal(10) ** 6),
    ("kk", Decimal(10) ** 6),
    ("м", Decimal(10) ** 6),
    ("m", Decimal(10) ** 6),
    ("к", Decimal(10) ** 3),
    ("k", Decimal(10) ** 3),
    ("b", Decimal(10) ** 9),
)
_MULTIPLIER_TOKENS_BY_LENGTH: Final = tuple(
    sorted((token for token, _ in _MULTIPLIERS), key=len, reverse=True)
)
_MULTIPLIER_ALTERNATION: Final = "|".join(re.escape(t) for t in _MULTIPLIER_TOKENS_BY_LENGTH)
_MULTIPLIER_SUFFIX_RE: Final = re.compile(f"(?:{_MULTIPLIER_ALTERNATION})$", re.IGNORECASE)

#: A comma is a thousands separator when every group after it has exactly
#: three digits (`1,500,000`); otherwise it is a decimal point (`1,5`).
_COMMA_THOUSANDS_RE: Final = re.compile(r"^\d{1,3}(?:,\d{3})+$")

#: What is left after currency/multiplier/separator handling must be a plain
#: (optionally decimal) number, or the input is rejected.
_PLAIN_NUMBER_RE: Final = re.compile(r"\d+(?:\.\d+)?")

#: A single number "phrase" inside a larger arithmetic expression: optional
#: leading currency, digits with internal separators, optional decimal part,
#: optional multiplier suffix, optional trailing currency.
_NUMBER_SPAN_RE: Final = re.compile(
    rf"(?:{_CURRENCY_TOKEN}\s*)?\d[\d _]*(?:[.,]\d+)?"
    rf"(?:{_MULTIPLIER_ALTERNATION})?(?:\s*{_CURRENCY_TOKEN})?",
    re.IGNORECASE,
)

_MAX_INPUT_LENGTH: Final = 256

_NARROW_NBSP: Final = " "

_COMPACT_UNITS: Final[tuple[tuple[Decimal, str], ...]] = (
    (Decimal(10) ** 9, "ккк"),
    (Decimal(10) ** 6, "кк"),
    (Decimal(10) ** 3, "к"),
)

# --- internal helpers --------------------------------------------------------


def _normalize_spaces(text: str) -> str:
    """Collapse every Unicode space variant to a plain ASCII space."""
    return unicodedata.normalize("NFKC", text).translate(_SPACE_TRANSLATION)


def _strip_currency(text: str) -> str:
    """Remove a currency marker from the start and/or end of *text*."""
    text = _CURRENCY_PREFIX_RE.sub("", text, count=1)
    text = _CURRENCY_SUFFIX_RE.sub("", text, count=1)
    return text.strip()


def _extract_multiplier(text: str) -> tuple[str, Decimal]:
    """Split a trailing к/кк/ккк/k/kk/kkk/m/м/b suffix off *text*."""
    match = _MULTIPLIER_SUFFIX_RE.search(text)
    if match is None:
        return text, Decimal(1)
    suffix = match.group(0).lower()
    multiplier = next(value for token, value in _MULTIPLIERS if token.lower() == suffix)
    return text[: match.start()], multiplier


def _normalize_decimal_separator(text: str) -> str:
    """Turn a comma into a decimal point, unless it groups thousands."""
    if "," not in text:
        return text
    if "." not in text and _COMMA_THOUSANDS_RE.fullmatch(text):
        return text.replace(",", "")
    return text.replace(",", ".")


def _parse_number_token(token: str) -> Decimal:
    """Parse one self-contained number phrase (with optional currency/multiplier).

    Raises:
        AmountParseError: If the token cannot be interpreted unambiguously.
    """
    text = _strip_currency(token)
    text = _THOUSANDS_GLUE_RE.sub("", text)
    text, multiplier = _extract_multiplier(text)
    text = _normalize_decimal_separator(text)
    if not _PLAIN_NUMBER_RE.fullmatch(text):
        raise AmountParseError(f"cannot parse amount: {token!r}")
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise AmountParseError(f"cannot parse amount: {token!r}") from exc
    return value * multiplier


# --- public API ---------------------------------------------------------


def parse_amount(raw: str) -> Decimal:
    """Parse a money value written in any common human format.

    Supports: "10 000", "10 000", "299 900 ₽", "299900руб", "1 500 000 р.",
    "1,5кк", "1.5kk", "10к", "250k", "3ккк", "1_000_000", "299,900".

    Args:
        raw: User-supplied text.

    Returns:
        The parsed amount as a `Decimal`.

    Raises:
        AmountParseError: If the string cannot be interpreted unambiguously.
    """
    if len(raw) > _MAX_INPUT_LENGTH:
        raise AmountParseError("amount string is too long")
    text = _normalize_spaces(raw).strip()
    if not text:
        raise AmountParseError("empty amount string")
    return _parse_number_token(text)


_ALLOWED_AST_NODES: Final = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Constant,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.USub,
    ast.UAdd,
)

_MAX_AST_DEPTH: Final = 32
_MAX_POWER_EXPONENT: Final = 12


def evaluate_amount(expression: str) -> Decimal:
    """Evaluate an arithmetic expression over money values.

    Example:
        "299 900 ₽ + 10000" -> Decimal("309900")
        "1.5кк * 3 - 250к"  -> Decimal("4250000")

    Allowed operators: `+ - * / // % ** ()` and unary minus. `eval`/`exec`
    are never used: the expression is parsed into an AST and walked with a
    node-type whitelist.

    Args:
        expression: User-supplied arithmetic expression; money tokens may use
            any format supported by `parse_amount`.

    Returns:
        The computed result as a `Decimal`.

    Raises:
        AmountParseError: If the expression is too long, malformed, uses a
            disallowed construct, or a money token cannot be parsed.
    """
    if len(expression) > _MAX_INPUT_LENGTH:
        raise AmountParseError("expression is too long")

    normalized = _normalize_spaces(expression).strip()
    if not normalized:
        raise AmountParseError("empty expression")

    def _replace(match: re.Match[str]) -> str:
        return format(_parse_number_token(match.group(0)), "f")

    clean = _NUMBER_SPAN_RE.sub(_replace, normalized)

    try:
        tree = ast.parse(clean, mode="eval")
    except SyntaxError as exc:
        raise AmountParseError(f"invalid expression: {expression!r}") from exc

    _check_ast(tree)

    try:
        return _eval_node(tree.body)
    except ZeroDivisionError as exc:
        raise AmountParseError("division by zero") from exc


def _walk_with_depth(node: ast.AST, depth: int = 0) -> Iterator[tuple[int, ast.AST]]:
    """Yield every node in the tree paired with its depth from the root."""
    yield depth, node
    for child in ast.iter_child_nodes(node):
        yield from _walk_with_depth(child, depth + 1)


def _check_ast(tree: ast.AST) -> None:
    """Reject any node type, constant type, depth or exponent outside the whitelist."""
    for depth, node in _walk_with_depth(tree):
        if depth > _MAX_AST_DEPTH:
            raise AmountParseError("expression is too deeply nested")
        if not isinstance(node, _ALLOWED_AST_NODES):
            raise AmountParseError(f"disallowed expression element: {type(node).__name__}")
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, int | float):
                raise AmountParseError("only numeric literals are allowed")
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            exponent = node.right
            if not (isinstance(exponent, ast.Constant) and isinstance(exponent.value, int)):
                raise AmountParseError("exponent must be a plain integer literal")
            if not (0 <= exponent.value <= _MAX_POWER_EXPONENT):
                raise AmountParseError("exponent out of allowed range")


def _eval_node(node: ast.expr) -> Decimal:
    """Evaluate an already-validated AST expression node into a `Decimal`."""
    if isinstance(node, ast.Constant):
        return Decimal(str(node.value))
    if isinstance(node, ast.UnaryOp):
        operand = _eval_node(node.operand)
        return -operand if isinstance(node.op, ast.USub) else operand
    if isinstance(node, ast.BinOp):
        return _apply_binop(node.op, _eval_node(node.left), _eval_node(node.right))
    raise AmountParseError(f"disallowed expression element: {type(node).__name__}")


def _apply_binop(op: ast.operator, left: Decimal, right: Decimal) -> Decimal:
    """Apply one whitelisted binary operator to two already-evaluated operands."""
    if isinstance(op, ast.Add):
        return left + right
    if isinstance(op, ast.Sub):
        return left - right
    if isinstance(op, ast.Mult):
        return left * right
    if isinstance(op, ast.Div):
        return left / right
    if isinstance(op, ast.FloorDiv):
        return left // right
    if isinstance(op, ast.Mod):
        return left % right
    if isinstance(op, ast.Pow):
        return left ** int(right)
    raise AmountParseError(f"disallowed operator: {type(op).__name__}")


def format_amount(value: Decimal | int, *, currency: bool = True) -> str:
    """Format a value for display: 299900 -> '299 900 ₽' (narrow no-break space).

    Args:
        value: Amount to format. Fractional values are rounded to the
            nearest whole unit; in-game currency has no fractional part.
        currency: Whether to append the "₽" suffix.

    Returns:
        Human-readable string using narrow-no-break-space thousands grouping.
    """
    whole = int(Decimal(value).to_integral_value(rounding=ROUND_HALF_UP))
    grouped = f"{whole:,}".replace(",", _NARROW_NBSP)
    return f"{grouped}{_NARROW_NBSP}₽" if currency else grouped


def format_compact(value: Decimal | int) -> str:
    """Format a value compactly for tight spaces: 1500000 -> '1.5 кк'.

    Args:
        value: Amount to format.

    Returns:
        A compact string using к/кк/ккк suffixes, or the plain integer if the
        magnitude is below 1 000.
    """
    amount = Decimal(value)
    negative = amount < 0
    magnitude = abs(amount)

    for unit, suffix in _COMPACT_UNITS:
        if magnitude >= unit:
            text = f"{_quantize_one_decimal(magnitude / unit)} {suffix}"
            break
    else:
        text = str(int(magnitude.to_integral_value(rounding=ROUND_HALF_UP)))

    return f"-{text}" if negative else text


def _quantize_one_decimal(value: Decimal) -> str:
    """Round to one decimal place and drop a trailing ".0"."""
    text = f"{value.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP):f}"
    return text.removesuffix(".0")
