"""Project-wide invariants from PLAN.md §7.3 / §16, checked once here rather
than trusted by convention.
"""

import ast
from pathlib import Path

from stalbot.infrastructure.sheets.protection import ensure_writable

_SRC_ROOT = Path(__file__).resolve().parents[4] / "src" / "stalbot"


def _all_source_files() -> list[Path]:
    return sorted(_SRC_ROOT.rglob("*.py"))


def test_user_entered_value_input_option_never_appears_as_a_string_literal() -> None:
    """PLAN.md §7.3: `valueInputOption=RAW` always, `USER_ENTERED` never.

    Only flags `"USER_ENTERED"`/`'USER_ENTERED'` as an actual string
    literal — prose in docstrings/comments explaining the constraint (in
    backticks, no Python quotes) is exactly what should be there.
    """
    offenders = [
        path
        for path in _all_source_files()
        if '"USER_ENTERED"' in path.read_text(encoding="utf-8")
        or "'USER_ENTERED'" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_batch_update_is_only_ever_reached_through_the_protected_client() -> None:
    """Every `.batch_update(`/`.write_verified(` call site is inside `SheetsClient` itself.

    `SheetsClient.batch_update` is the single choke point that calls
    `protection.ensure_writable` before any network request (PLAN.md §7.3).
    As soon as another module starts calling Sheets writes directly, this
    fails — forcing that new call site through the same client instead of
    growing a second, unprotected write path.
    """
    offending_calls: list[str] = []
    for path in _all_source_files():
        if path.name == "client.py" and path.parent.name == "sheets":
            continue  # the protected client itself
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"batch_update", "write_verified", "values_batch_update"}
            ):
                offending_calls.append(f"{path.relative_to(_SRC_ROOT)}:{node.lineno}")
    assert offending_calls == []


def test_ensure_writable_accepts_every_literal_range_client_py_ever_builds() -> None:
    """Belt-and-braces: replay `ensure_writable` over the write ranges the
    project's own tests exercise against `SheetsClient`, so this file fails
    first if a future milestone starts writing a formula-owned column.
    """
    known_safe_writes = [
        "DataBase!A3:E3",  # /add — Тикеты raw columns
        "DataBase!H3",  # /add — referrer
        "DataBase!I3",  # /add — Discord ID binding
        "DataBase!Q3",  # booster flag
        "DataBase!AA3:AG3",  # item database CRUD
    ]
    for ref in known_safe_writes:
        ensure_writable(ref)  # must not raise
