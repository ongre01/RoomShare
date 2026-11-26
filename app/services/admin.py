"""Protected callers' management operations, with atomic writes and minimal audit data."""

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from app.db import DEFAULT_BUSY_TIMEOUT_MS, connect, write_transaction
from app.services.date_policy import Clock, KST, load_booking_window, month_dates, parse_date, parse_dates, utc_now
from app.services.errors import DateConflictError, InvalidInputError, InvalidPolicyError, database_errors
from app.services.reservations import find_conflicts, occupy_dates


class AdminNotFoundError(Exception):
    code = "NOT_FOUND"

    def __init__(self):
        super().__init__("Management resource does not exist")


class AdminChangeNotAllowedError(Exception):
    code = "CHANGE_NOT_ALLOWED"

    def __init__(self):
        super().__init__("This management change is not allowed")


_RESERVATION_COLUMNS = "id, name, memo, status, created_at, cancelled_at"
_ACTIONS = {"CREATE", "CANCEL", "ADMIN_UPDATE", "ADMIN_CANCEL", "BLOCK", "UNBLOCK", "SETTINGS"}


def _now(clock: Clock) -> datetime:
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _text(value: str, field: str, maximum: int, *, required: bool = False) -> str:
    if not isinstance(value, str):
        raise InvalidInputError(field)
    if required:
        value = value.strip()
    if len(value) > maximum or (required and not value):
        raise InvalidInputError(field)
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise InvalidInputError(field) from None
    return value


def _reservation(connection: sqlite3.Connection, id: str) -> sqlite3.Row:
    if not isinstance(id, str) or len(id) != 36 or not id.isascii():
        raise AdminNotFoundError()
    row = connection.execute(
        f"SELECT {_RESERVATION_COLUMNS} FROM reservations WHERE id = ?", (id,),
    ).fetchone()
    if row is None:
        raise AdminNotFoundError()
    return row


def _active_dates(connection: sqlite3.Connection, id: str) -> list[str]:
    return [row[0] for row in connection.execute(
        "SELECT date FROM reserved_dates WHERE reservation_id = ? AND type = ? ORDER BY date",
        (id, "RESERVED"),
    )]


def _date_detail(value: str) -> dict:
    """Only return recognized date snapshots; never forward arbitrary stored JSON."""
    try:
        detail = json.loads(value)
        if not isinstance(detail, dict):
            raise ValueError
        result = {}
        for key in ("before", "after"):
            dates = detail[key]
            if not isinstance(dates, list):
                raise ValueError
            result[key] = [parse_date(item).isoformat() for item in dates]
        return result
    except (KeyError, TypeError, ValueError, RecursionError):
        raise InvalidPolicyError("Invalid audit date snapshot") from None


def _dates(connection: sqlite3.Connection, row: sqlite3.Row) -> list[str]:
    if row["status"] == "CONFIRMED":
        return _active_dates(connection, row["id"])
    log = connection.execute(
        "SELECT detail FROM reservation_logs WHERE reservation_id = ? AND action IN (?, ?) "
        "ORDER BY id DESC LIMIT 1", (row["id"], "CANCEL", "ADMIN_CANCEL"),
    ).fetchone()
    if log is None:
        raise InvalidPolicyError("Missing cancellation snapshot")
    return sorted(_date_detail(log[0])["before"])


def _append_log(connection, *, reservation_id=None, action, before, after, created_at):
    if not connection.in_transaction:
        raise RuntimeError("Management log requires an active transaction")
    connection.execute(
        "INSERT INTO reservation_logs (reservation_id, action, detail, created_at) VALUES (?, ?, ?, ?)",
        (reservation_id, action, json.dumps({"before": before, "after": after}, separators=(",", ":")), created_at),
    )


def list_reservations(
    db_path: str | Path, month: str, *, status: str = "CONFIRMED",
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> dict:
    period = month_dates(month)
    if status not in ("CONFIRMED", "CANCELLED", "ALL"):
        raise InvalidInputError("status")
    first, last = period[0].isoformat(), period[-1].isoformat()
    result = []
    with database_errors(), closing(connect(db_path, busy_timeout_ms=busy_timeout_ms)) as connection:
        connection.execute("BEGIN")
        rows = connection.execute(
            f"SELECT {_RESERVATION_COLUMNS} FROM reservations WHERE "
            "(? = 'ALL' OR status = ?) AND (status = 'CANCELLED' OR id IN "
            "(SELECT reservation_id FROM reserved_dates WHERE date BETWEEN ? AND ?)) "
            "ORDER BY created_at, id", (status, status, first, last),
        ).fetchall()
        for row in rows:
            dates = _dates(connection, row)
            if any(first <= value <= last for value in dates):
                result.append({**dict(row), "dates": dates})
    return {"reservations": result}


def update_reservation(
    db_path: str | Path, *, id: str, changes: dict, clock: Clock = utc_now,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> dict:
    if not isinstance(changes, dict) or not changes or changes.keys() - {"name", "memo", "dates"}:
        raise InvalidInputError("changes")
    normalized = dict(changes)
    if "name" in normalized:
        normalized["name"] = _text(normalized["name"], "name", 40, required=True)
    if "memo" in normalized:
        normalized["memo"] = _text(normalized["memo"], "memo", 200)
    parsed = parse_dates(normalized["dates"]) if "dates" in normalized else None
    with database_errors(), write_transaction(db_path, busy_timeout_ms=busy_timeout_ms) as connection:
        row = _reservation(connection, id)
        before = _active_dates(connection, id)
        now = _now(clock)
        if row["status"] != "CONFIRMED" or not before or any(parse_date(day) < now.astimezone(KST).date() for day in before):
            raise AdminChangeNotAllowedError()
        after = [day.isoformat() for day in parsed] if parsed is not None else before
        additions = tuple(parse_date(day) for day in after if day not in before)
        if additions:
            load_booking_window(connection, lambda: now).validate(additions)
        conflicts = find_conflicts(connection, [day.isoformat() for day in additions])
        if conflicts:
            raise DateConflictError(conflicts)
        name, memo = normalized.get("name", row["name"]), normalized.get("memo", row["memo"])
        if after != before or name != row["name"] or memo != row["memo"]:
            connection.execute("DELETE FROM reserved_dates WHERE reservation_id = ?", (id,))
            occupy_dates(connection, id, after)
            connection.execute("UPDATE reservations SET name = ?, memo = ? WHERE id = ?", (name, memo, id))
            _append_log(connection, reservation_id=id, action="ADMIN_UPDATE", before=before, after=after, created_at=now.isoformat())
        result = {**dict(_reservation(connection, id)), "dates": after}
    return result


def cancel_reservation(
    db_path: str | Path, *, id: str, clock: Clock = utc_now,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> dict:
    with database_errors(), write_transaction(db_path, busy_timeout_ms=busy_timeout_ms) as connection:
        row = _reservation(connection, id)
        if row["status"] != "CANCELLED":
            before = _active_dates(connection, id)
            now = _now(clock)
            if not before or any(parse_date(day) < now.astimezone(KST).date() for day in before):
                raise AdminChangeNotAllowedError()
            connection.execute("DELETE FROM reserved_dates WHERE reservation_id = ?", (id,))
            connection.execute(
                "UPDATE reservations SET status = ?, cancelled_at = ? WHERE id = ?",
                ("CANCELLED", now.isoformat(), id),
            )
            _append_log(connection, reservation_id=id, action="ADMIN_CANCEL", before=before, after=[], created_at=now.isoformat())
    return {"id": id, "status": "CANCELLED"}


def block_dates(
    db_path: str | Path, *, dates: list[str], memo: str = "", clock: Clock = utc_now,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> dict:
    parsed = parse_dates(dates)
    memo = _text(memo, "memo", 200)
    normalized = [day.isoformat() for day in parsed]
    with database_errors(), write_transaction(db_path, busy_timeout_ms=busy_timeout_ms) as connection:
        window = load_booking_window(connection, clock)
        window.validate(parsed)
        conflicts = find_conflicts(connection, normalized)
        if conflicts:
            raise DateConflictError(conflicts)
        for day in normalized:
            try:
                connection.execute(
                    "INSERT INTO reserved_dates (date, type, memo) VALUES (?, ?, ?)",
                    (day, "BLOCKED", memo),
                )
            except sqlite3.IntegrityError as error:
                if error.sqlite_errorcode == sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY and str(error) == "UNIQUE constraint failed: reserved_dates.date":
                    raise DateConflictError((day,)) from None
                raise
        _append_log(connection, action="BLOCK", before=[], after=normalized, created_at=window.now.isoformat())
    return {"dates": normalized, "status": "BLOCKED"}


def unblock_date(
    db_path: str | Path, date: str, *, clock: Clock = utc_now,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> dict:
    parsed = parse_date(date)
    with database_errors(), write_transaction(db_path, busy_timeout_ms=busy_timeout_ms) as connection:
        row = connection.execute("SELECT type FROM reserved_dates WHERE date = ?", (date,)).fetchone()
        if row is None or row["type"] != "BLOCKED":
            raise AdminNotFoundError()
        now = _now(clock)
        if parsed < now.astimezone(KST).date():
            raise AdminChangeNotAllowedError()
        connection.execute("DELETE FROM reserved_dates WHERE date = ? AND type = ?", (date, "BLOCKED"))
        _append_log(connection, action="UNBLOCK", before=[date], after=[], created_at=now.isoformat())
    return {"date": date, "status": "AVAILABLE"}


def _setting(connection) -> int:
    row = connection.execute("SELECT value FROM settings WHERE key = ?", ("booking_horizon_days",)).fetchone()
    if row is None or not isinstance(row[0], str) or not row[0].isascii() or not row[0].isdecimal() or row[0].startswith("0"):
        raise InvalidPolicyError("Invalid booking horizon")
    try:
        return int(row[0])
    except ValueError:
        raise InvalidPolicyError("Invalid booking horizon") from None


def get_settings(db_path: str | Path, *, busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS) -> dict:
    with database_errors(), closing(connect(db_path, busy_timeout_ms=busy_timeout_ms)) as connection:
        return {"booking_horizon_days": _setting(connection)}


def update_settings(
    db_path: str | Path, *, booking_horizon_days: int, clock: Clock = utc_now,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> dict:
    if type(booking_horizon_days) is not int or not 1 <= booking_horizon_days <= 366:
        raise InvalidInputError("booking_horizon_days")
    with database_errors(), write_transaction(db_path, busy_timeout_ms=busy_timeout_ms) as connection:
        before = _setting(connection)
        if before != booking_horizon_days:
            now = _now(clock)
            connection.execute("UPDATE settings SET value = ? WHERE key = ?", (str(booking_horizon_days), "booking_horizon_days"))
            # Also reject values that cannot be represented by the current calendar.
            load_booking_window(connection, lambda: now)
            _append_log(connection, action="SETTINGS", before=before, after=booking_horizon_days, created_at=now.isoformat())
    return {"booking_horizon_days": booking_horizon_days}


def _log_detail(row) -> dict:
    if row["action"] == "SETTINGS":
        try:
            detail = json.loads(row["detail"])
            if not isinstance(detail, dict) or any(type(detail[key]) is not int or detail[key] < 1 for key in ("before", "after")):
                raise ValueError
            return {key: detail[key] for key in ("before", "after")}
        except (KeyError, TypeError, ValueError, RecursionError):
            raise InvalidPolicyError("Invalid settings audit snapshot") from None
    if row["action"] not in _ACTIONS:
        raise InvalidPolicyError("Unknown audit action")
    return _date_detail(row["detail"])


def get_logs(
    db_path: str | Path, *, page: int = 1, page_size: int = 50,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> dict:
    if type(page) is not int or not 1 <= page <= 2_147_483_647:
        raise InvalidInputError("page")
    if type(page_size) is not int or not 1 <= page_size <= 100:
        raise InvalidInputError("page_size")
    with database_errors(), closing(connect(db_path, busy_timeout_ms=busy_timeout_ms)) as connection:
        connection.execute("BEGIN")
        total = connection.execute("SELECT COUNT(*) FROM reservation_logs").fetchone()[0]
        rows = connection.execute(
            "SELECT id, reservation_id, action, detail, created_at FROM reservation_logs "
            "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?", (page_size, (page - 1) * page_size),
        ).fetchall()
        logs = [{**dict(row), "detail": _log_detail(row)} for row in rows]
    return {"logs": logs, "page": page, "page_size": page_size, "total": total}
