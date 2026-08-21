"""A fixed-instant `Clock` for tests (sqlite_migration.md Часть XI).

Structurally satisfies `application.ports.clock.Clock` — every service test
that needs a deterministic "now" injects one of these instead of touching
the wall clock. Was duplicated as `_FixedClock` in 9 test files before this
module existed.
"""

from datetime import datetime


class FakeClock:
    """Always returns the `datetime` it was constructed with."""

    def __init__(self, now: datetime) -> None:
        """Fix the clock at `now`.

        Args:
            now: The instant every `.now()` call will return.
        """
        self._now = now

    def now(self) -> datetime:
        """Return the fixed instant this clock was built with."""
        return self._now
