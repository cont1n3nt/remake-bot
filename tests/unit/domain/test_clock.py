"""Tests for `stalbot.domain.clock` (PLAN.md §5.2, §11.4, §13)."""

from datetime import UTC, date, datetime, timedelta

import pytest

from stalbot.domain.clock import (
    GMT3,
    DateRange,
    format_date,
    format_datetime,
    format_duration,
    parse_date,
    parse_deadline,
    parse_sheet_datetime,
)
from stalbot.domain.errors import DeadlineParseError, InvalidPeriodError

NOW: datetime = datetime(2026, 7, 31, 21, 45, tzinfo=GMT3)


def test_format_date() -> None:
    assert format_date(date(2026, 7, 31)) == "31.07.2026"


def test_format_datetime_converts_to_gmt3() -> None:
    utc_value = datetime(2026, 7, 31, 18, 45, tzinfo=UTC)
    assert format_datetime(utc_value) == "31.07.2026 21:45"


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "0 с"),
        (42, "42 с"),
        (59, "59 с"),
        (60, "1 мин"),
        (90, "1 мин"),
        (3600, "1 ч"),
        (3665, "1 ч 1 мин"),
        (86400, "1 д"),
        (86400 + 3600 * 3 + 60 * 15, "1 д 3 ч 15 мин"),
    ],
)
def test_format_duration(seconds: int, expected: str) -> None:
    assert format_duration(seconds) == expected


class TestDateRange:
    def test_day(self) -> None:
        rng = DateRange.day(date(2026, 7, 31))
        assert rng.start == rng.end == date(2026, 7, 31)

    def test_week(self) -> None:
        rng = DateRange.week(date(2026, 7, 1), date(2026, 7, 7), today=date(2026, 7, 7))
        assert rng.start == date(2026, 7, 1)
        assert rng.end == date(2026, 7, 7)

    def test_month_regular(self) -> None:
        rng = DateRange.month(2026, 7)
        assert rng.start == date(2026, 7, 1)
        assert rng.end == date(2026, 7, 31)

    def test_month_february_leap_year(self) -> None:
        rng = DateRange.month(2028, 2)
        assert rng.end == date(2028, 2, 29)

    def test_month_december_rolls_into_next_year(self) -> None:
        rng = DateRange.month(2026, 12)
        assert rng.start == date(2026, 12, 1)
        assert rng.end == date(2026, 12, 31)

    def test_end_before_start_raises(self) -> None:
        with pytest.raises(InvalidPeriodError):
            DateRange.week(date(2026, 7, 10), date(2026, 7, 1), today=date(2026, 7, 10))

    def test_week_rejects_a_future_end_date(self) -> None:
        with pytest.raises(InvalidPeriodError):
            DateRange.week(date(2026, 7, 1), date(2026, 7, 7), today=date(2026, 7, 6))

    def test_week_rejects_a_range_over_31_days(self) -> None:
        with pytest.raises(InvalidPeriodError):
            DateRange.week(date(2026, 6, 1), date(2026, 7, 31), today=date(2026, 7, 31))

    def test_week_allows_end_equal_to_today(self) -> None:
        rng = DateRange.week(date(2026, 7, 1), date(2026, 7, 7), today=date(2026, 7, 7))
        assert rng.end == date(2026, 7, 7)

    def test_week_allows_exactly_31_days(self) -> None:
        rng = DateRange.week(date(2026, 7, 1), date(2026, 7, 31), today=date(2026, 7, 31))
        assert (rng.end - rng.start).days + 1 == 31

    def test_contains(self) -> None:
        rng = DateRange.month(2026, 7)
        assert rng.contains(date(2026, 7, 15))
        assert not rng.contains(date(2026, 8, 1))


class TestParseDeadline:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("01.08.2026 21:00", datetime(2026, 8, 1, 21, 0, tzinfo=GMT3)),
            ("01.08.26 21:00", datetime(2026, 8, 1, 21, 0, tzinfo=GMT3)),
            ("01.08 21:00", datetime(2026, 8, 1, 21, 0, tzinfo=GMT3)),
            ("01/08/2026 21:00", datetime(2026, 8, 1, 21, 0, tzinfo=GMT3)),
            ("01-08-2026 21:00", datetime(2026, 8, 1, 21, 0, tzinfo=GMT3)),
            ("01.08.2026", datetime(2026, 8, 1, 23, 59, tzinfo=GMT3)),
            ("завтра 20:00", datetime(2026, 8, 1, 20, 0, tzinfo=GMT3)),
            ("завтра", datetime(2026, 8, 1, 23, 59, tzinfo=GMT3)),
            ("сегодня 22:30", datetime(2026, 7, 31, 22, 30, tzinfo=GMT3)),
            ("через 3 часа", NOW + timedelta(hours=3)),
            ("через 1 час", NOW + timedelta(hours=1)),
            ("через 30 минут", NOW + timedelta(minutes=30)),
            ("  01.08.2026 21:00  ", datetime(2026, 8, 1, 21, 0, tzinfo=GMT3)),
            ("ЗАВТРА 20:00", datetime(2026, 8, 1, 20, 0, tzinfo=GMT3)),
        ],
    )
    def test_valid(self, raw: str, expected: datetime) -> None:
        assert parse_deadline(raw, now=NOW) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "   ",
            "not a date",
            "31.07.2026 21:00",  # before now (21:45) -> not strictly future
            "30.07.2026 12:00",  # in the past
            "32.13.2026 21:00",  # invalid calendar date
            "01.01.2027 00:00",  # more than 90 days ahead
            # DOM-2/SEC-2: an unbounded digit run overflows timedelta's C-int
            # internals (OverflowError) both at construction and at `now + ...`.
            "через 999999999999999999999999999999 часов",
            "через 999999999999999999999999999999 минут",
        ],
    )
    def test_invalid(self, raw: str) -> None:
        with pytest.raises(DeadlineParseError):
            parse_deadline(raw, now=NOW)


class TestParseDate:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("31.07.2026", date(2026, 7, 31)),
            ("01.01.2026", date(2026, 1, 1)),
            ("  31.07.2026  ", date(2026, 7, 31)),
            ("29.02.2028", date(2028, 2, 29)),  # leap year
        ],
    )
    def test_valid(self, raw: str, expected: date) -> None:
        assert parse_date(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "not a date",
            "31.07.26",  # two-digit year not accepted, unlike parse_deadline
            "31.07",  # missing year
            "31/07/2026",  # wrong separator, unlike parse_deadline
            "32.07.2026",  # invalid day
            "31.13.2026",  # invalid month
            "29.02.2026",  # not a leap year
            "31.07.2026 21:00",  # no time component accepted
        ],
    )
    def test_invalid(self, raw: str) -> None:
        with pytest.raises(InvalidPeriodError):
            parse_date(raw)


class TestParseSheetDatetime:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("31.07.2026 21:45", datetime(2026, 7, 31, 21, 45, tzinfo=GMT3)),
            ("31.07.26 02:56", datetime(2026, 7, 31, 2, 56, tzinfo=GMT3)),
            ("1.8.2026", datetime(2026, 8, 1, 0, 0, tzinfo=GMT3)),
        ],
    )
    def test_valid(self, raw: str, expected: datetime) -> None:
        assert parse_sheet_datetime(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "   ",
            "not a date",
            "31.07",  # no year at all — parse_deadline may default it, this must not
            "32.13.2026 21:00",  # invalid calendar date
            # DOM-5: a 3-digit year is ambiguous, not a typo`d 2- or 4-digit one —
            # must be rejected (None), not silently parsed as literal year 202.
            "31.7.202 02:56",
            "1.1.100",
        ],
    )
    def test_invalid_returns_none(self, raw: str) -> None:
        assert parse_sheet_datetime(raw) is None
