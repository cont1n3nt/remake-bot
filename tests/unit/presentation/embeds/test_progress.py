"""Tests for `stalbot.presentation.embeds.progress`."""

import pytest

from stalbot.presentation.embeds.progress import render_progress_bar


@pytest.mark.parametrize(
    ("done", "need", "expected"),
    [
        (0, 100, "▱▱▱▱▱▱▱▱▱▱ 0 %"),
        (52, 100, "▰▰▰▰▰▱▱▱▱▱ 52 %"),
        (100, 100, "▰▰▰▰▰▰▰▰▰▰ 100 %"),
        (150, 100, "▰▰▰▰▰▰▰▰▰▰ 100 %"),  # clamped, never overflows
        (3780, 7000, "▰▰▰▰▰▱▱▱▱▱ 54 %"),
    ],
)
def test_render_progress_bar(done: int, need: int, expected: str) -> None:
    assert render_progress_bar(done, need) == expected


def test_render_progress_bar_rejects_non_positive_need() -> None:
    with pytest.raises(ValueError, match="need must be positive"):
        render_progress_bar(1, 0)
