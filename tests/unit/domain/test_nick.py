"""Tests for `stalbot.domain.nick` (PLAN.md §6.3)."""

import pytest

from stalbot.domain.nick import normalize_nick


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Scaryyyyy", "scaryyyyy"),
        ("scaryyyyy", "scaryyyyy"),
        ("SCARYYYYY", "scaryyyyy"),
        ("  Scaryyyyy  ", "scaryyyyy"),
        ("Scary   yyyyy", "scary yyyyy"),
        ("Scary\tyyyyy", "scary yyyyy"),
        ("Scary\nyyyyy", "scary yyyyy"),
        ("", ""),
        ("   ", ""),
        ("Иван Петров", "иван петров"),
    ],
)
def test_normalize_nick(raw: str, expected: str) -> None:
    assert normalize_nick(raw) == expected


def test_normalize_nick_is_idempotent() -> None:
    once = normalize_nick("  Scary   Yyyyy  ")
    twice = normalize_nick(once)
    assert once == twice
