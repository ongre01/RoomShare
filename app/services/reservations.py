"""Atomic reservation creation and connection-sharing write helpers."""

from collections.abc import Sequence
import json
from pathlib import Path
import sqlite3
from typing import Literal, TypedDict
from uuid import uuid4

from app.db import DEFAULT_BUSY_TIMEOUT_MS, write_transaction
from app.services.date_policy import Clock, load_booking_window, parse_dates, utc_now
from app.services.errors import DateConflictError, InvalidInputError, database_errors
from app.services.tokens import generate_token, hash_token


class ReservationResult(TypedDict):
    id: str
    dates: list[str]
    status: Literal["CONFIRMED"]
    cancel_token: str


def _require_transaction(connection: sqlite3.Connection) -> None:
    if not connection.in_transaction:
        raise RuntimeError("Reservation helpers require an active transaction")


def find_conflicts(connection: sqlite3.Connection, dates: Sequence[str]) -> tuple[str, ...]:
    """Return only requested occupied dates, without SQLite's IN-variable limit."""
    _require_transaction(connection)
    if not dates:
        return ()
    requested = set(dates)
    rows = connection.execute(
        "SELECT date FROM reserved_dates WHERE date >= ? AND date <= ? ORDER BY date",
        (min(dates), max(dates)),
    )
    return tuple(row[0] for row in rows if row[0] in requested)


def occupy_dates(
    connection: sqlite3.Connection, reservation_id: str, dates: Sequence[str],
) -> None:
    """Insert validated RESERVED dates; caller owns validation and rollback.

    Restrict classification to this exact INSERT constraint. A UUID collision,
    FK/CHECK failure or an unrelated trigger constraint is not a date conflict.
    """
    _require_transaction(connection)
    for value in dates:
        try:
            connection.execute(
                "INSERT INTO reserved_dates (date, reservation_id, type) VALUES (?, ?, ?)",
                (value, reservation_id, "RESERVED"),
            )
        except sqlite3.IntegrityError as error:
            if (
                error.sqlite_errorcode == sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY
                and str(error) == "UNIQUE constraint failed: reserved_dates.date"
            ):
                raise DateConflictError((value,)) from None
            raise


def append_creation_log(
    connection: sqlite3.Connection, reservation_id: str, dates: Sequence[str], created_at: str,
) -> None:
    """Write only a date snapshot. Never accept names, memos or token material."""
    _require_transaction(connection)
    connection.execute(
        "INSERT INTO reservation_logs (reservation_id, action, detail, created_at) VALUES (?, ?, ?, ?)",
        (
            reservation_id, "CREATE",
            json.dumps({"before": [], "after": sorted(dates)}, separators=(",", ":")),
            created_at,
        ),
    )


def create_reservation(
    db_path: str | Path,
    *,
    name: str,
    dates: list[str],
    memo: str = "",
    clock: Clock = utc_now,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> ReservationResult:
    """Commit one booking, all occupancy and its audit entry, then return.

    The caller must not log the returned secret. No retries, HTTP responses,
    configuration fallback, cancellation or administrator actions live here.
    """
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 40:
        raise InvalidInputError("name")
    name = name.strip()
    if not isinstance(memo, str) or len(memo) > 200:
        raise InvalidInputError("memo")
    parsed = parse_dates(dates)
    normalized = [value.isoformat() for value in parsed]
    with database_errors():
        with write_transaction(db_path, busy_timeout_ms=busy_timeout_ms) as connection:
            window = load_booking_window(connection, clock)
            window.validate(parsed)
            conflicts = find_conflicts(connection, normalized)
            if conflicts:
                raise DateConflictError(conflicts)
            reservation_id = str(uuid4())
            token = generate_token()
            created_at = window.now.isoformat()
            connection.execute(
                "INSERT INTO reservations (id, name, memo, cancel_token_hash, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (reservation_id, name, memo, hash_token(token), "CONFIRMED", created_at),
            )
            occupy_dates(connection, reservation_id, normalized)
            append_creation_log(connection, reservation_id, normalized, created_at)
    return {"id": reservation_id, "dates": normalized, "status": "CONFIRMED", "cancel_token": token}
