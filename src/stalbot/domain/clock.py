"""Time handling: the project runs on a single fixed timezone, GMT+3.

No naive `datetime.now()` may appear anywhere in the project (enforced by the
ruff `DTZ` rules); every date coming from Sheets or user input is parsed into
a `date`/`datetime` carrying `tzinfo=GMT3` (see PLAN.md §5.2).
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Final

from stalbot.domain.errors import DeadlineParseError, InvalidPeriodError

#: The only timezone used anywhere in the project.
GMT3: Final = timezone(timedelta(hours=3))

_DATE_FORMAT: Final = "%d.%m.%Y"
_DATETIME_FORMAT: Final = "%d.%m.%Y %H:%M"

#: `/week`'s business rule (PLAN.md §10.11): an explicit range may not span
#: more days than this.
_WEEK_MAX_DAYS: Final = 31

_DEFAULT_DEADLINE_HOUR: Final = 23
_DEFAULT_DEADLINE_MINUTE: Final = 59
_MAX_DEADLINE_DAYS_AHEAD: Final = 90


class SystemClock:
    """Time source backed by the real wall clock, pinned to `GMT3`.

    Structurally satisfies `application.ports.clock.Clock`.
    """

    def now(self) -> datetime:
        """Return `datetime.now()` pinned to `GMT3`."""
        return datetime.now(GMT3)

    def today(self) -> date:
        """Return today's date in `GMT3`."""
        return self.now().date()


def format_date(value: date) -> str:
    """Format a date the one way the project ever displays it: `31.07.2026`."""
    return value.strftime(_DATE_FORMAT)


def format_datetime(value: datetime) -> str:
    """Format a datetime the one way the project ever displays it.

    The value is converted to `GMT3` before formatting, so a caller can pass
    any tz-aware datetime and still get the project's canonical
    `31.07.2026 21:45` representation.

    Args:
        value: A tz-aware datetime.

    Returns:
        `"31.07.2026 21:45"`.
    """
    return value.astimezone(GMT3).strftime(_DATETIME_FORMAT)


def format_duration(seconds: float) -> str:
    """Format a duration compactly: `"2 д 3 ч 15 мин"`, `"5 мин"`, `"42 с"`.

    Used for `/healthcheck`'s uptime and cache-age fields (PLAN.md §12, M11)
    — plain seconds are unreadable at bot-uptime scale.

    Args:
        seconds: A non-negative duration in seconds.
    """
    total = int(seconds)
    days, remainder = divmod(total, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)

    parts: list[str] = []
    if days:
        parts.append(f"{days} д")
    if hours:
        parts.append(f"{hours} ч")
    if minutes:
        parts.append(f"{minutes} мин")
    if not parts:
        parts.append(f"{secs} с")
    return " ".join(parts)


@dataclass(frozen=True, slots=True)
class DateRange:
    """An inclusive `[start, end]` date range."""

    start: date
    end: date

    def __post_init__(self) -> None:
        """Reject a range whose end precedes its start."""
        if self.end < self.start:
            raise InvalidPeriodError(f"range end {self.end} is before start {self.start}")

    @classmethod
    def day(cls, day: date) -> "DateRange":
        """Build a single-day range."""
        return cls(start=day, end=day)

    @classmethod
    def week(cls, start: date, end: date, *, today: date) -> "DateRange":
        """Build a range from an explicit start/end pair (`/week`'s command).

        Args:
            start: First day of the range, inclusive.
            end: Last day of the range, inclusive.
            today: The caller's current date — *end* may not be after it.

        Raises:
            InvalidPeriodError: If *end* precedes *start*, *end* is in the
                future, or the range spans more than `_WEEK_MAX_DAYS` days.
        """
        if end > today:
            raise InvalidPeriodError("конец периода не может быть в будущем")
        range_ = cls(start=start, end=end)
        if (end - start).days + 1 > _WEEK_MAX_DAYS:
            raise InvalidPeriodError(f"диапазон не может превышать {_WEEK_MAX_DAYS} дней")
        return range_

    @classmethod
    def month(cls, year: int, month: int) -> "DateRange":
        """Build a range covering an entire calendar month."""
        start = date(year, month, 1)
        end = date(year, 12, 31) if month == 12 else date(year, month + 1, 1) - timedelta(days=1)
        return cls(start=start, end=end)

    def contains(self, value: date) -> bool:
        """Return whether *value* falls within this range (inclusive)."""
        return self.start <= value <= self.end


# --- parse_deadline -----------------------------------------------------

_RELATIVE_HOURS_RE: Final = re.compile(r"^через\s+(\d+)\s*час(?:а|ов)?$", re.IGNORECASE)
_RELATIVE_MINUTES_RE: Final = re.compile(r"^через\s+(\d+)\s*минут[уы]?$", re.IGNORECASE)
_RELATIVE_DAY_RE: Final = re.compile(r"^(завтра|сегодня)(?:\s+(\d{1,2}):(\d{2}))?$", re.IGNORECASE)
#: The year group only accepts exactly 2 or 4 digits (DOM-5) — `\d{2,4}` also
#: matched a bare 3-digit run (e.g. "202"), which is nobody's real year and
#: not an unambiguous typo of a 2- or 4-digit one either; it used to be
#: accepted as literal year 202 instead of being rejected.
_ABSOLUTE_RE: Final = re.compile(
    r"^(?P<day>\d{1,2})[./-](?P<month>\d{1,2})(?:[./-](?P<year>\d{4}|\d{2}))?"
    r"(?:\s+(?P<hour>\d{1,2}):(?P<minute>\d{2}))?$"
)

_DEADLINE_HINT: Final = (
    "не удалось распознать дату. Примеры: 31.07.2026 21:00, 31.07 21:00, завтра 20:00, через 3 часа"
)


def parse_deadline(raw: str, *, now: datetime) -> datetime:
    """Parse a ticket deadline typed into a Discord modal text field.

    Supports absolute dates (`31.07.2026 21:00`, `31.07.26 21:00`, `31.07
    21:00` with the current year, `31.07.2026` defaulting the time to
    `23:59`, with `.`, `/` or `-` as equivalent separators) and relative
    phrases (`завтра 20:00`, `сегодня 22:30`, `через 3 часа`).

    Args:
        raw: Text typed by the user.
        now: The current moment (tz-aware, `GMT3`) — injected rather than
            read from the wall clock so the function stays pure and testable.

    Returns:
        A tz-aware `datetime` in `GMT3`, strictly after *now* and no more
        than 90 days ahead.

    Raises:
        DeadlineParseError: If the text cannot be parsed, or the resulting
            moment is not in that future window.
    """
    text = raw.strip().lower()
    if not text:
        raise DeadlineParseError(_DEADLINE_HINT)

    deadline = (
        _parse_relative_hours(text, now)
        or _parse_relative_minutes(text, now)
        or _parse_relative_day(text, now)
        or _parse_absolute(text, now)
    )
    if deadline is None:
        raise DeadlineParseError(_DEADLINE_HINT)

    if deadline <= now:
        raise DeadlineParseError("указанная дата уже прошла, укажите время в будущем")
    if deadline > now + timedelta(days=_MAX_DEADLINE_DAYS_AHEAD):
        raise DeadlineParseError(
            f"срок не может превышать {_MAX_DEADLINE_DAYS_AHEAD} дней от текущего момента"
        )
    return deadline


def _parse_relative_hours(text: str, now: datetime) -> datetime | None:
    match = _RELATIVE_HOURS_RE.match(text)
    if match is None:
        return None
    try:
        return now + timedelta(hours=int(match.group(1)))
    except OverflowError as exc:
        # An unbounded digit run (the regex has no length cap) overflows
        # timedelta's underlying C-int representation, either at
        # construction or at the addition above (DOM-2/SEC-2).
        raise DeadlineParseError(_DEADLINE_HINT) from exc


def _parse_relative_minutes(text: str, now: datetime) -> datetime | None:
    match = _RELATIVE_MINUTES_RE.match(text)
    if match is None:
        return None
    try:
        return now + timedelta(minutes=int(match.group(1)))
    except OverflowError as exc:
        raise DeadlineParseError(_DEADLINE_HINT) from exc


def _parse_relative_day(text: str, now: datetime) -> datetime | None:
    match = _RELATIVE_DAY_RE.match(text)
    if match is None:
        return None
    word, hour, minute = match.groups()
    base = now.date() + (timedelta(days=1) if word == "завтра" else timedelta())
    h = int(hour) if hour is not None else _DEFAULT_DEADLINE_HOUR
    m = int(minute) if minute is not None else _DEFAULT_DEADLINE_MINUTE
    return _combine(base, h, m)


def _parse_absolute(text: str, now: datetime) -> datetime | None:
    match = _ABSOLUTE_RE.match(text)
    if match is None:
        return None
    day = int(match.group("day"))
    month = int(match.group("month"))
    year_text = match.group("year")
    year = now.year if year_text is None else _expand_year(int(year_text))
    hour_text, minute_text = match.group("hour"), match.group("minute")
    hour = _DEFAULT_DEADLINE_HOUR if hour_text is None else int(hour_text)
    minute = _DEFAULT_DEADLINE_MINUTE if minute_text is None else int(minute_text)
    try:
        base = date(year, month, day)
    except ValueError as exc:
        raise DeadlineParseError(_DEADLINE_HINT) from exc
    return _combine(base, hour, minute)


def _expand_year(year: int) -> int:
    return year + 2000 if year < 100 else year


def _combine(day: date, hour: int, minute: int) -> datetime:
    try:
        return datetime(day.year, day.month, day.day, hour, minute, tzinfo=GMT3)
    except ValueError as exc:
        raise DeadlineParseError(_DEADLINE_HINT) from exc


def parse_date(raw: str) -> date:
    """Parse a strict `ДД.ММ.ГГГГ` date, as typed into `/day`, `/week` (PLAN.md §10.11).

    Unlike `parse_deadline`, this accepts no relative phrases, defaults no
    missing year, and applies no future/range validation — those bounds
    differ per command and are the caller's job.

    Args:
        raw: Text typed by the user, e.g. `"31.07.2026"`.

    Raises:
        InvalidPeriodError: If the text does not match `ДД.ММ.ГГГГ` or names
            a calendar date that does not exist.
    """
    text = raw.strip()
    parts = text.split(".")
    hint = f"не удалось распознать дату «{raw}», ожидается формат ДД.ММ.ГГГГ"
    if len(parts) != 3:
        raise InvalidPeriodError(hint)
    day_text, month_text, year_text = parts
    if not (day_text.isdigit() and month_text.isdigit() and year_text.isdigit()):
        raise InvalidPeriodError(hint)
    if len(year_text) != 4:
        raise InvalidPeriodError(hint)
    try:
        return date(int(year_text), int(month_text), int(day_text))
    except ValueError as exc:
        raise InvalidPeriodError(hint) from exc


def parse_sheet_datetime(raw: str) -> datetime | None:
    """Parse a `Дата` cell from the `Тикеты` block into a tz-aware datetime.

    Unlike `parse_deadline`, this never raises and applies no future/range
    validation — a transaction date is a historical fact, not a deadline.
    Some legacy rows have the column blank (it was only backfilled
    recently); this returns `None` for those so `CacheSync` can skip them
    without failing the whole sync, rather than inventing a fake date.

    Args:
        raw: The cell's text, e.g. `"31.07.26 02:56"`.

    Returns:
        A tz-aware `datetime` in `GMT3`, or `None` if `raw` is blank or does
        not match the `Д.М.ГГ[ГГ] [Ч:М]` shape (year required, unlike
        `parse_deadline`, since there is no "current year" to default to
        for a historical row).
    """
    text = raw.strip()
    if not text:
        return None
    match = _ABSOLUTE_RE.match(text)
    if match is None or match.group("year") is None:
        return None
    day, month = int(match.group("day")), int(match.group("month"))
    year = _expand_year(int(match.group("year")))
    hour_text, minute_text = match.group("hour"), match.group("minute")
    hour = int(hour_text) if hour_text is not None else 0
    minute = int(minute_text) if minute_text is not None else 0
    try:
        return datetime(year, month, day, hour, minute, tzinfo=GMT3)
    except ValueError:
        return None
