"""Text progress bars used in profile/referral embeds (PLAN.md §5.3)."""

from typing import Final

_SEGMENTS: Final = 10
_FILLED: Final = "\N{BLACK PARALLELOGRAM}"
_EMPTY: Final = "\N{WHITE PARALLELOGRAM}"


def render_progress_bar(done: int, need: int) -> str:
    """Render a 10-segment progress bar: `▰▰▰▰▰▱▱▱▱▱ 52 %`.

    Args:
        done: Current progress amount.
        need: Amount required to reach 100%.

    Returns:
        A fixed-width bar followed by the rounded percentage.

    Raises:
        ValueError: If *need* is not positive.
    """
    if need <= 0:
        raise ValueError("need must be positive")
    ratio = max(0.0, min(1.0, done / need))
    filled = round(ratio * _SEGMENTS)
    bar = _FILLED * filled + _EMPTY * (_SEGMENTS - filled)
    percent = round(ratio * 100)
    return f"{bar} {percent} %"
