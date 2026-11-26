"""Token-authenticated, atomic cancellation of an entire future reservation."""

from datetime import datetime, timezone
import hmac
import json
from pathlib import Path
from typing import Literal, TypedDict

from app.db import DEFAULT_BUSY_TIMEOUT_MS, write_transaction
from app.services.date_policy import Clock, KST, parse_date, utc_now
from app.services.errors import database_errors
from app.services.tokens import hash_token


class InvalidCancellationCredentialsError(Exception):
    code = "CANCELLATION_NOT_FOUND"

    def __init__(self):
        super().__init__("Reservation credentials are invalid")


class CancellationNotAllowedError(Exception):
    code = "CANCELLATION_NOT_ALLOWED"

    def __init__(self):
        super().__init__("Only entirely future reservations can be cancelled")


class CancellationResult(TypedDict):
    id: str
    status: Literal["CANCELLED"]


def cancel_reservation(
    db_path: str | Path,
    *,
    id: str,
    cancel_token: str,
    clock: Clock = utc_now,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> CancellationResult:
    """Authenticate and cancel under one writer lock; never expose credentials.

    A valid repeat returns the same response without another write or log. The
    booking horizon is deliberately irrelevant: existing bookings can outlive
    a later reduction in the window. There is no automatic retry.
    """
    if not isinstance(id, str) or not isinstance(cancel_token, str):
        raise InvalidCancellationCredentialsError()
    with database_errors():
        with write_transaction(db_path, busy_timeout_ms=busy_timeout_ms) as connection:
            # Invalid strings still follow the absent-ID path. In particular,
            # unpaired Unicode surrogates must never reach SQLite's encoder.
            lookup_id = id if len(id) == 36 and id.isascii() else ""
            row = connection.execute(
                "SELECT id, cancel_token_hash, status FROM reservations WHERE id = ?", (lookup_id,),
            ).fetchone()
            try:
                submitted_hash = hash_token(cancel_token)
            except UnicodeEncodeError:
                raise InvalidCancellationCredentialsError() from None
            # Do the same fixed-size comparison for absent and existing IDs.
            expected_hash = row["cancel_token_hash"] if row is not None else "0" * 64
            matches = hmac.compare_digest(submitted_hash, expected_hash)
            if row is None or not matches:
                raise InvalidCancellationCredentialsError()
            if row["status"] != "CANCELLED":
                now = clock()
                if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
                    raise ValueError("Clock must return a timezone-aware datetime")
                now = now.astimezone(timezone.utc)
                today = now.astimezone(KST).date()
                dates = [
                    item[0] for item in connection.execute(
                        "SELECT date FROM reserved_dates WHERE reservation_id = ? ORDER BY date", (id,),
                    )
                ]
                if not dates or any(parse_date(value) <= today for value in dates):
                    raise CancellationNotAllowedError()
                connection.execute("DELETE FROM reserved_dates WHERE reservation_id = ?", (id,))
                connection.execute(
                    "UPDATE reservations SET status = ?, cancelled_at = ? WHERE id = ?",
                    ("CANCELLED", now.isoformat(), id),
                )
                connection.execute(
                    "INSERT INTO reservation_logs (reservation_id, action, detail, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (id, "CANCEL", json.dumps({"before": dates, "after": []}, separators=(",", ":")),
                     now.isoformat()),
                )
    # Returning after the context also covers commit failures and connection cleanup.
    return {"id": id, "status": "CANCELLED"}
