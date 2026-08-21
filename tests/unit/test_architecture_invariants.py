"""Project-wide invariants, checked once here rather than trusted by convention.

sqlite_migration.md, Часть XI: replaces the Sheets-era
`test_write_safety_invariants.py` (removed with the Sheets layer at Э9) —
now that SQLite is the only write path, the invariants worth guarding are
about the cache layer instead of `gspread`.
"""

import ast
import re
from pathlib import Path

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "stalbot"
_REPOSITORIES_DIR = _SRC_ROOT / "infrastructure" / "cache" / "repositories"
_POSTERS_DIR = _SRC_ROOT / "infrastructure" / "posters"


def _all_source_files() -> list[Path]:
    return sorted(_SRC_ROOT.rglob("*.py"))


def test_deals_and_players_are_only_ever_inserted_from_repositories() -> None:
    """`INSERT INTO deals|players` may only appear inside `infrastructure/cache/repositories/`.

    Those two tables carry a player's real money/XP history — a write from
    anywhere else would bypass the repository layer's transaction handling
    and money-rounding conventions (`domain/money.to_storage`).
    """
    pattern = re.compile(r"INSERT\s+INTO\s+(deals|players)\b", re.IGNORECASE)
    offenders = [
        f"{path.relative_to(_SRC_ROOT)}"
        for path in _all_source_files()
        if pattern.search(path.read_text(encoding="utf-8"))
        and _REPOSITORIES_DIR not in path.parents
    ]
    assert offenders == []


def test_sql_is_never_assembled_with_an_f_string_except_placeholder_runs() -> None:
    """No f-string builds a SQL query, except a `",".join("?" * n)` placeholder run.

    An f-string interpolating anything else into a query string is exactly
    the shape of a SQL-injection bug — the placeholder-run exception is
    already reviewed case by case (each site carries a `# noqa: S608` with
    a comment explaining why only `?` characters are interpolated).
    """
    sql_keyword = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE)\b", re.IGNORECASE)
    offenders: list[str] = []
    for path in _all_source_files():
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.JoinedStr):
                continue
            # Only the f-string's own literal text can spell a SQL keyword —
            # an interpolated `{expr}` is a Python identifier, not query
            # text, so checking it too would flag e.g. `self._selected_ids`
            # (contains "select") as if it were the word "SELECT".
            literal_text = "".join(
                value.value
                for value in node.values
                if isinstance(value, ast.Constant) and isinstance(value.value, str)
            )
            if not sql_keyword.search(literal_text):
                continue
            # Sanctioned only if every interpolated `{expr}` is a bare name
            # (a `?`-placeholder-run local, or a module-level constant) —
            # never a computed expression, call, or attribute access, which
            # is what would let runtime/user data flow into the query text.
            interpolated = [v for v in node.values if isinstance(v, ast.FormattedValue)]
            if interpolated and all(isinstance(v.value, ast.Name) for v in interpolated):
                continue
            offenders.append(f"{path.relative_to(_SRC_ROOT)}:{node.lineno}")
    assert offenders == []


def test_pil_is_only_ever_imported_from_the_posters_layer() -> None:
    """`PIL`/`Pillow` may only be imported from `infrastructure/posters/`.

    Keeps the heavy imaging dependency confined to one module (Часть IX) —
    every other layer stays importable/testable without it. The directory
    doesn't exist yet (Э11 is still ahead), so today this just asserts PIL
    isn't already leaking in early.
    """
    offenders = [
        f"{path.relative_to(_SRC_ROOT)}"
        for path in _all_source_files()
        if re.search(r"^\s*(import PIL|from PIL)", path.read_text(encoding="utf-8"), re.MULTILINE)
        and _POSTERS_DIR not in path.parents
    ]
    assert offenders == []


def test_rub_and_kopeks_suffixed_values_are_never_added_in_the_same_expression() -> None:
    """`Rub`/`Kopeks` amounts never appear on both sides of a `+`/`-` in one line.

    The type system (`NewType`) already blocks this at the mypy level —
    this is the belt-and-braces duplicate sqlite_migration.md Часть XI
    asks for, catching a line that a `# type: ignore` or an untyped local
    slipped past. Matched on the naming convention (`*_kopeks` locals vs.
    `price_buy`/`price_sell`/`.amount`, the project's `Rub`-denominated
    field names), not full type inference.
    """
    kopeks_name = r"\w*_kopeks\w*"
    rub_name = r"(?:\w*\.)?(?:price_buy|price_sell|amount)\b"
    mixed = re.compile(rf"({kopeks_name}\s*[+-]\s*{rub_name})|({rub_name}\s*[+-]\s*{kopeks_name})")
    offenders: list[str] = []
    for path in _all_source_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if mixed.search(line):
                offenders.append(f"{path.relative_to(_SRC_ROOT)}:{lineno}")
    assert offenders == []
