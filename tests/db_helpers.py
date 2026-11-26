"""Stable snapshots and service-level invariants for isolated SQLite tests."""

import sqlite3


RESERVATION_ID = "11111111-1111-4111-8111-111111111111"
OTHER_RESERVATION_ID = "22222222-2222-4222-8222-222222222222"
CREATED_AT = "2026-11-30T15:00:00Z"


def insert_reservation(
    connection: sqlite3.Connection,
    reservation_id: str = RESERVATION_ID,
    *,
    status: str = "CONFIRMED",
) -> str:
    """Insert synthetic data; cancellation token generation belongs to RS-003."""
    connection.execute(
        "INSERT INTO reservations "
        "(id, name, memo, cancel_token_hash, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (reservation_id, "QA_USER_A", "QA_MEMO_SENTINEL", "a" * 64, status, CREATED_AT),
    )
    return reservation_id


def snapshot(connection: sqlite3.Connection) -> dict[str, list[tuple]]:
    """Compare all persisted logical rows, including migration metadata."""
    # These table/order names are static test identifiers, never external input.
    tables = {
        "reservations": "id",
        "reserved_dates": "date",
        "reservation_logs": "id",
        "settings": "key",
        "schema_migrations": "version",
    }
    return {
        table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY {order}")]
        for table, order in tables.items()
    }


def assert_db_invariants(connection: sqlite3.Connection) -> None:
    """Check DB integrity and the occupancy rules later services must preserve."""
    assert [row[0] for row in connection.execute("PRAGMA integrity_check")] == ["ok"]
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert connection.execute(
        "SELECT date FROM reserved_dates GROUP BY date HAVING COUNT(*) > 1"
    ).fetchall() == []
    assert connection.execute(
        "SELECT d.date FROM reserved_dates AS d "
        "LEFT JOIN reservations AS r ON r.id = d.reservation_id "
        "WHERE d.type = 'RESERVED' AND (r.id IS NULL OR r.status != 'CONFIRMED')"
    ).fetchall() == []
    assert connection.execute(
        "SELECT date FROM reserved_dates WHERE type = 'BLOCKED' AND reservation_id IS NOT NULL"
    ).fetchall() == []
    assert connection.execute(
        "SELECT r.id FROM reservations AS r JOIN reserved_dates AS d ON d.reservation_id = r.id "
        "WHERE r.status = 'CANCELLED'"
    ).fetchall() == []
