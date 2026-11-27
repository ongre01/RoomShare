"""Monthly public calendar, with no reservation identifiers or personal data."""

from contextlib import closing
from pathlib import Path
from typing import Literal, TypedDict

from app.db import DEFAULT_BUSY_TIMEOUT_MS, connect
from app.services.date_policy import Clock, load_booking_window, month_dates, utc_now
from app.services.errors import database_errors


class CalendarDay(TypedDict):
    date: str
    status: Literal["AVAILABLE", "RESERVED", "BLOCKED", "PAST"]


class CalendarResult(TypedDict):
    dates: list[CalendarDay]
    today: str
    min_date: str
    max_date: str


def get_calendar(
    db_path: str | Path,
    month: str,
    *,
    clock: Clock = utc_now,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> CalendarResult:
    days = month_dates(month)
    with database_errors():
        with closing(connect(db_path, busy_timeout_ms=busy_timeout_ms)) as connection:
            connection.execute("BEGIN")
            window = load_booking_window(connection, clock)
            occupied = {
                row["date"]: row["type"] for row in connection.execute(
                    "SELECT date, type FROM reserved_dates WHERE date >= ? AND date <= ?",
                    (days[0].isoformat(), days[-1].isoformat()),
                )
            }
            # Closing the owned connection also ends this read-only snapshot.
    return {
        "dates": [
            {"date": day.isoformat(), "status": "PAST" if day < window.today
             else occupied.get(day.isoformat(), "AVAILABLE")}
            for day in days
        ],
        "today": window.today.isoformat(),
        "min_date": window.today.isoformat(),
        "max_date": window.max_date.isoformat(),
    }
