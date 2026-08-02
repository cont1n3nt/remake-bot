"""Clock port (see PLAN.md §3.1)."""

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    """Time source, always tz-aware `GMT3`.

    Injected wherever code needs "now" so tests can supply a fixed instant
    instead of touching the wall clock. `domain.clock.SystemClock`
    structurally satisfies this protocol.
    """

    def now(self) -> datetime:
        """Return the current moment, tz-aware in `GMT3`."""
        ...
