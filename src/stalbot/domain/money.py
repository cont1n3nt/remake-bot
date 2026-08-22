"""Single source of truth for parsing, evaluating and formatting money values.

Used by `/add`, ticket confirmation, `/setprice`, `/setboost`, `/new_price`,
boost order calculations and the future `/calc` command. Nowhere else in the
project should a raw `int(...)`/`float(...)` be built from user-supplied
money text (see PLAN.md §5.1).
"""

import ast
import math
import re
import unicodedata
from collections.abc import Iterator, Mapping
from decimal import ROUND_HALF_UP, Decimal, DecimalException, InvalidOperation, localcontext
from typing import Final, NewType

from stalbot.domain.errors import AmountParseError

# --- storage-boundary units (sqlite_migration.md §III.3, §IV.1) -------------
#
# Two units of money live in the trading schema (Э3+), and mypy --strict must
# never let them mix: `Rub` — whole rubles, `deals.amount`/`catalog_items`
# price columns (§III.3: "Торговые деньги — INTEGER целых рублей" — deal
# amounts have no fractional part on the sheet, and SQL aggregates like
# `SUM(...) FILTER (...)` need a plain INTEGER column to work at all). A
# third unit, `Kopeks` (shelter costs, Э5 — 0.86 ₽/energy unit needs
# sub-ruble precision `Rub` can't hold) is declared here too, ahead of its
# first real use, so both units exist together from day one and can never
# be confused for one another even before Kopeks has a table.
Rub = NewType("Rub", int)
Kopeks = NewType("Kopeks", int)

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

#: Decimal context precision used for every parse/evaluate call (DOM-3):
#: the ambient default context caps arithmetic at 28 significant digits,
#: which silently rounds a multiplier multiplication or `evaluate_amount`
#: binop above that width instead of raising — well within reach of the
#: 256-character input limit. 60 leaves comfortable headroom above any
#: realistic amount while still catching genuinely absurd input elsewhere
#: (AST depth/length guards, `SEC-1`'s finite-result check).
#:
#: `localcontext()` at every call site below only overrides `prec` — `Emax`/
#: `Emin`/the trap set (`InvalidOperation`/`DivisionByZero`/`Overflow`, all
#: trapped) stay at the stdlib default. SEC-1's `except DecimalException`
#: depends on that: it is what turns an exponent explosion (nested `** 12`
#: terms, each individually within `_MAX_POWER_EXPONENT`) into a raised
#: `decimal.Overflow` instead of a silently wrong result. Do not widen
#: `Emax` or disable a trap here without re-auditing that guarantee.
_ARITHMETIC_PRECISION: Final = 60

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
    with localcontext() as ctx:
        ctx.prec = _ARITHMETIC_PRECISION
        return _parse_number_token(text)


_ALLOWED_AST_NODES: Final = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Constant,
    ast.Name,
    ast.Load,
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

#: Placeholder identifier prefix substituted for money tokens whose formatted
#: text would otherwise be parsed as a native Python float literal (anything
#: with a decimal point) — see `evaluate_amount`.
_TOKEN_PLACEHOLDER: Final = "__m{}__"

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

    Money tokens with a fractional part are never handed to `ast.parse` as
    literal text: Python has no fixed-point literal grammar, so any token
    with a decimal point would be parsed as a native `float`, silently
    losing precision above ~15-17 significant digits before it is ever
    converted back to `Decimal`. Instead each such token is replaced with a
    placeholder identifier bound to its exact, already-parsed `Decimal` in
    `tokens`, and `_eval_node` resolves it by lookup rather than by reading
    `ast.Constant.value`. Whole-number tokens are left as plain integer
    literals — Python `int` is arbitrary-precision, so they round-trip
    exactly regardless of magnitude.

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

    tokens: dict[str, Decimal] = {}

    def _replace(match: re.Match[str]) -> str:
        value = _parse_number_token(match.group(0))
        text = format(value, "f")
        if "." not in text:
            return text
        name = _TOKEN_PLACEHOLDER.format(len(tokens))
        tokens[name] = value
        return name

    with localcontext() as ctx:
        ctx.prec = _ARITHMETIC_PRECISION
        clean = _NUMBER_SPAN_RE.sub(_replace, normalized)

        try:
            tree = ast.parse(clean, mode="eval")
        except SyntaxError as exc:
            raise AmountParseError(f"invalid expression: {expression!r}") from exc

        _check_ast(tree)

        try:
            result = _eval_node(tree.body, tokens)
        except ZeroDivisionError as exc:
            raise AmountParseError("division by zero") from exc
        except DecimalException as exc:
            # SEC-1: legitimate-looking arithmetic can still overflow the
            # context's exponent range (e.g. a handful of nested `** 12`
            # terms, each individually within `_MAX_POWER_EXPONENT`) and
            # raise `decimal.Overflow`/`InvalidOperation` — an internal
            # exception type that must never reach the caller unwrapped.
            raise AmountParseError(f"invalid arithmetic result: {expression!r}") from exc
        if not result.is_finite():
            # Not currently reachable: the `Overflow`/`InvalidOperation` traps
            # this context inherits (see `_ARITHMETIC_PRECISION`) mean layer 1
            # (`_check_ast`'s `ast.Constant` check) or layer 2 (the
            # `DecimalException` catch above) already catches every path we
            # could construct. Kept as insurance against a future change to
            # that context (e.g. `_ARITHMETIC_PRECISION` widening `Emax` or a
            # trap getting disabled) silently reopening this gap.
            raise AmountParseError(f"amount is not finite: {expression!r}")
        return result


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
            if isinstance(node.value, float) and not math.isfinite(node.value):
                # SEC-1: Python's own grammar recognizes scientific notation
                # ("1e400") as a float literal that `_NUMBER_SPAN_RE` never
                # sees (it has no `e`-exponent handling), so it reaches
                # `ast.parse` unmodified — one big enough overflows to `inf`
                # silently, no `SyntaxError`, and the type check above lets
                # it through (`inf` is a legitimate `float`).
                raise AmountParseError("amount is not finite")
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            exponent = node.right
            if not (isinstance(exponent, ast.Constant) and isinstance(exponent.value, int)):
                raise AmountParseError("exponent must be a plain integer literal")
            if not (0 <= exponent.value <= _MAX_POWER_EXPONENT):
                raise AmountParseError("exponent out of allowed range")


def _eval_node(node: ast.expr, tokens: Mapping[str, Decimal]) -> Decimal:
    """Evaluate an already-validated AST expression node into a `Decimal`."""
    if isinstance(node, ast.Constant):
        return Decimal(str(node.value))
    if isinstance(node, ast.Name):
        try:
            return tokens[node.id]
        except KeyError as exc:
            raise AmountParseError(f"unknown identifier: {node.id!r}") from exc
    if isinstance(node, ast.UnaryOp):
        operand = _eval_node(node.operand, tokens)
        return -operand if isinstance(node.op, ast.USub) else operand
    if isinstance(node, ast.BinOp):
        return _apply_binop(node.op, _eval_node(node.left, tokens), _eval_node(node.right, tokens))
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


def round_for_storage(value: Decimal) -> Decimal:
    """Round a money value to a whole unit before it is written to Sheets.

    Every write site that stores an `int(...)` amount/price column must
    round through this first — game currency has no fractional part on the
    sheet, and truncating with a bare `int(...)` (round-toward-zero) instead
    of rounding would make the persisted value quietly diverge from the
    `Decimal` that stays cached/returned to the caller for the very same
    field. Uses the same `ROUND_HALF_UP` convention as `format_amount`.

    Args:
        value: Amount to round.

    Returns:
        `value` rounded to the nearest whole unit, still a `Decimal` — the
        object a caller caches/returns should be built from this, not from
        the original unrounded `value`, so the two never disagree.
    """
    return value.to_integral_value(rounding=ROUND_HALF_UP)


def to_storage(value: Decimal) -> Rub:
    """Round a ruble amount to the `INTEGER` form the trading schema stores (§III.3).

    The one place a `Rub` value is allowed to come into existence — every
    write to `deals.amount`/`catalog_items.price_buy`/`price_sell` must
    go through this, not a bare `int(...)`, so rounding is always
    `ROUND_HALF_UP` (matching `round_for_storage`/`format_amount`) rather
    than silently truncating toward zero.

    Args:
        value: Amount to store, in whole-or-fractional rubles.
    """
    return Rub(int(round_for_storage(value)))


def from_storage(value: Rub | int) -> Decimal:
    """Convert a stored `Rub` `INTEGER` back into a `Decimal` for display/arithmetic.

    Args:
        value: A `deals.amount`/`catalog_items` price column value.
    """
    return Decimal(int(value))


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


def format_kopeks(value: Kopeks | int) -> str:
    """Format a `Kopeks` shelter-cost value for display: 186 -> '1,86 ₽'.

    Unlike `format_amount`, this keeps the sub-ruble precision `Kopeks`
    exists for (§V.2: 0.86 ₽/energy unit) instead of rounding to a whole
    ruble — a crafting cost that's a fraction of a ruble is still real, not
    noise to round away.

    Args:
        value: A `shelter_items`/`shelter_cost` kopeks column value.
    """
    rubles, remainder = divmod(int(value), 100)
    grouped = f"{rubles:,}".replace(",", _NARROW_NBSP)
    return f"{grouped},{remainder:02d}{_NARROW_NBSP}₽"


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

    for index, (unit, suffix) in enumerate(_COMPACT_UNITS):
        if magnitude < unit:
            continue
        # Rounding the quotient to one decimal can itself reach 1000 (e.g.
        # 999 950 -> "1000.0к"), which is really the next unit up, not
        # "1000 к" (DOM-4). Escalate as long as a larger unit exists.
        # _COMPACT_UNITS is ordered largest-first, so decrementing `index`
        # is what moves to a larger unit; this only matters if a unit is
        # ever inserted out of that order.
        while index > 0 and _quantized_quotient(magnitude, unit) >= 1000:
            index -= 1
            unit, suffix = _COMPACT_UNITS[index]
        text = f"{_format_quotient(_quantized_quotient(magnitude, unit))} {suffix}"
        break
    else:
        text = str(int(magnitude.to_integral_value(rounding=ROUND_HALF_UP)))

    return f"-{text}" if negative else text


def _quantized_quotient(magnitude: Decimal, unit: Decimal) -> Decimal:
    """Round `magnitude / unit` to one decimal place."""
    return (magnitude / unit).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def _format_quotient(quantized: Decimal) -> str:
    """Render an already-quantized one-decimal value, dropping a trailing ".0"."""
    return f"{quantized:f}".removesuffix(".0")
