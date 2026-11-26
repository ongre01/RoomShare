"""Strict calendar dates and the current, transaction-scoped booking window."""

from calendar import monthrange
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import re
import sqlite3
from zoneinfo import ZoneInfo

from app.services.errors import InvalidInputError, InvalidPolicyError


Clock = Callable[[], datetime]
KST = ZoneInfo("Asia/Seoul")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_MONTH = re.compile(r"[0-9]{4}-[0-9]{2}")
_HORIZON = re.compile(r"[1-9][0-9]*")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_date(value: str) -> date:
    if not isinstance(value, str) or not _DATE.fullmatch(value):
        raise InvalidInputError("dates")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise InvalidInputError("dates") from None


def parse_dates(values: list[str]) -> tuple[date, ...]:
    if not isinstance(values, list) or not values:
        raise InvalidInputError("dates")
    parsed = tuple(parse_date(value) for value in values)
    if len(set(parsed)) != len(parsed):
        raise InvalidInputError("dates")
    return tuple(sorted(parsed))


def month_dates(month: str) -> tuple[date, ...]:
    if not isinstance(month, str) or not _MONTH.fullmatch(month):
        raise InvalidInputError("month")
    try:
        first = date.fromisoformat(month + "-01")
    except ValueError:
        raise InvalidInputError("month") from None
    return tuple(first.replace(day=day) for day in range(1, monthrange(first.year, first.month)[1] + 1))


@dataclass(frozen=True)
class BookingWindow:
    today: date
    max_date: date
    horizon_days: int
    now: datetime  # The same clock reading, normalized to UTC, for audit timestamps.

    def validate(self, dates: tuple[date, ...]) -> None:
        if (
            not dates or len(dates) > self.horizon_days
            or any(value < self.today or value > self.max_date for value in dates)
        ):
            raise InvalidInputError("dates")


def load_booking_window(connection: sqlite3.Connection, clock: Clock = utc_now) -> BookingWindow:
    """Read within the caller's transaction; do not cache settings or the clock."""
    if not connection.in_transaction:
        raise RuntimeError("Booking policy requires an active transaction")
    row = connection.execute(
        "SELECT value FROM settings WHERE key = ?", ("booking_horizon_days",),
    ).fetchone()
    if row is None or not isinstance(row[0], str) or not _HORIZON.fullmatch(row[0]):
        raise InvalidPolicyError("Invalid booking horizon")
    now = clock()
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Clock must return a timezone-aware datetime")
    now = now.astimezone(timezone.utc)
    today = now.astimezone(KST).date()
    try:
        horizon = int(row[0])
        maximum = today + timedelta(days=horizon - 1)
    except (ValueError, OverflowError):
        raise InvalidPolicyError("Invalid booking horizon") from None
    return BookingWindow(today, maximum, horizon, now)
