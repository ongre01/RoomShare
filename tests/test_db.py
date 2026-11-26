"""TC-002-04..10: real SQLite constraints, atomic writes and connection ownership."""

from contextlib import closing
from queue import Queue
import sqlite3
import threading
import time

import pytest

from app import db
from app.db import connect, write_transaction
from tests.db_helpers import (
    CREATED_AT,
    OTHER_RESERVATION_ID,
    RESERVATION_ID,
    assert_db_invariants,
    insert_reservation,
    snapshot,
)


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("id", ""),
        ("id", "not-a-uuid"),
        ("id", RESERVATION_ID.replace("-", "")),
        ("id", "abcdefab-cdef-4abc-8abc-abcdefabcdef".upper()),
        ("id", "g1111111-1111-4111-8111-111111111111"),
        ("id", "1111111-11111-4111-8111-111111111111"),
        ("id", RESERVATION_ID + "\x00suffix"),
        ("id", RESERVATION_ID.encode("ascii")),
        ("cancel_token_hash", ""),
        ("cancel_token_hash", "a" * 63),
        ("cancel_token_hash", "a" * 65),
        ("cancel_token_hash", "g" * 64),
        ("cancel_token_hash", "A" * 64),
        ("cancel_token_hash", "a" * 64 + "\x00suffix"),
        ("cancel_token_hash", b"a" * 64),
    ],
    ids=[
        "empty-uuid", "invalid-uuid", "uuid-no-hyphens", "uppercase-uuid", "uuid-nonhex",
        "uuid-misplaced-hyphen", "uuid-nul-suffix", "uuid-blob", "empty-hash", "short-hash", "long-hash",
        "hash-nonhex", "uppercase-hash", "hash-nul-suffix", "hash-blob",
    ],
)
def test_tc002_01_uuid_and_hash_constraints(migrated_db, column, value):
    values = {"id": RESERVATION_ID, "cancel_token_hash": "a" * 64}
    values[column] = value
    with pytest.raises(sqlite3.IntegrityError) as error:
        with write_transaction(migrated_db) as connection:
            connection.execute(
                "INSERT INTO reservations (id, name, cancel_token_hash, status, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (values["id"], "QA_USER_A", values["cancel_token_hash"], "CONFIRMED", CREATED_AT),
            )
    assert error.value.sqlite_errorcode == sqlite3.SQLITE_CONSTRAINT_CHECK
    with closing(connect(migrated_db)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM reservations").fetchone()[0] == 0


def test_tc002_01_canonical_uuid_and_hash_are_persisted(migrated_db):
    reservation_id = "abcdefab-cdef-4abc-8abc-abcdefabcdef"
    token_hash = "0123456789abcdef" * 4
    with write_transaction(migrated_db) as connection:
        connection.execute(
            "INSERT INTO reservations (id, name, cancel_token_hash, status, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (reservation_id, "QA_USER_A", token_hash, "CONFIRMED", CREATED_AT),
        )
    with closing(connect(migrated_db)) as connection:
        assert tuple(connection.execute(
            "SELECT id, cancel_token_hash, created_at FROM reservations"
        ).fetchone()) == (reservation_id, token_hash, CREATED_AT)
        assert_db_invariants(connection)


@pytest.mark.parametrize("existing_type", ["RESERVED", "BLOCKED"])
@pytest.mark.parametrize("new_type", ["RESERVED", "BLOCKED"])
def test_tc002_04_shared_date_primary_key(migrated_db, existing_type, new_type):
    with write_transaction(migrated_db) as connection:
        insert_reservation(connection)
        insert_reservation(connection, OTHER_RESERVATION_ID)
        connection.execute(
            "INSERT INTO reserved_dates (date, reservation_id, type) VALUES (?, ?, ?)",
            ("2026-12-02", RESERVATION_ID if existing_type == "RESERVED" else None, existing_type),
        )
    with closing(connect(migrated_db)) as connection:
        before = snapshot(connection)
    with pytest.raises(sqlite3.IntegrityError) as error:
        with write_transaction(migrated_db) as connection:
            connection.execute(
                "INSERT INTO reserved_dates (date, reservation_id, type) VALUES (?, ?, ?)",
                ("2026-12-02", OTHER_RESERVATION_ID if new_type == "RESERVED" else None, new_type),
            )
    assert error.value.sqlite_errorcode == sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY
    with closing(connect(migrated_db)) as connection:
        assert snapshot(connection) == before
        assert_db_invariants(connection)


@pytest.mark.parametrize(
    ("reservation_id", "date_type", "error_code"),
    [
        (OTHER_RESERVATION_ID, "RESERVED", sqlite3.SQLITE_CONSTRAINT_FOREIGNKEY),
        (None, "RESERVED", sqlite3.SQLITE_CONSTRAINT_CHECK),
        (RESERVATION_ID, "BLOCKED", sqlite3.SQLITE_CONSTRAINT_CHECK),
        (None, "UNKNOWN", sqlite3.SQLITE_CONSTRAINT_CHECK),
        (RESERVATION_ID, "UNKNOWN", sqlite3.SQLITE_CONSTRAINT_CHECK),
        (None, None, sqlite3.SQLITE_CONSTRAINT_NOTNULL),
    ],
    ids=["unknown-fk", "reserved-null", "blocked-fk", "unknown-null", "unknown-fk-type", "null-type"],
)
def test_tc002_05_foreign_key_and_type_constraints(migrated_db, reservation_id, date_type, error_code):
    with write_transaction(migrated_db) as connection:
        insert_reservation(connection)
    with closing(connect(migrated_db)) as connection:
        before = snapshot(connection)
    with pytest.raises(sqlite3.IntegrityError) as error:
        with write_transaction(migrated_db) as connection:
            connection.execute(
                "INSERT INTO reserved_dates (date, reservation_id, type) VALUES (?, ?, ?)",
                ("2026-12-02", reservation_id, date_type),
            )
    assert error.value.sqlite_errorcode == error_code
    with closing(connect(migrated_db)) as connection:
        assert snapshot(connection) == before
        assert_db_invariants(connection)


def test_tc002_05_valid_combinations_and_log_foreign_key(migrated_db):
    with write_transaction(migrated_db) as connection:
        insert_reservation(connection)
        connection.executemany(
            "INSERT INTO reserved_dates (date, reservation_id, type) VALUES (?, ?, ?)",
            [("2026-12-02", RESERVATION_ID, "RESERVED"), ("2026-12-03", None, "BLOCKED")],
        )
        connection.executemany(
            "INSERT INTO reservation_logs (reservation_id, action, detail, created_at) VALUES (?, ?, ?, ?)",
            [(RESERVATION_ID, "CREATE", '{"dates":["2026-12-02"]}', CREATED_AT),
             (None, "BLOCK", '{"dates":["2026-12-03"]}', CREATED_AT)],
        )
    with pytest.raises(sqlite3.IntegrityError) as error:
        with write_transaction(migrated_db) as connection:
            connection.execute(
                "INSERT INTO reservation_logs (reservation_id, action) VALUES (?, ?)",
                (OTHER_RESERVATION_ID, "CREATE"),
            )
    assert error.value.sqlite_errorcode == sqlite3.SQLITE_CONSTRAINT_FOREIGNKEY
    with pytest.raises(sqlite3.IntegrityError):
        with write_transaction(migrated_db) as connection:
            connection.execute("DELETE FROM reservations WHERE id = ?", (RESERVATION_ID,))
    with closing(connect(migrated_db)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM reserved_dates").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM reservation_logs").fetchone()[0] == 2
        assert_db_invariants(connection)


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO reserved_dates (date, type) VALUES (NULL, 'BLOCKED')",
        "INSERT INTO settings (key, value) VALUES (NULL, '1')",
        "INSERT INTO reservations (id, name, cancel_token_hash, status) "
        "VALUES (NULL, 'QA_USER_A', '" + "a" * 64 + "', 'CONFIRMED')",
    ],
    ids=["date-null-pk", "setting-null-pk", "reservation-null-pk"],
)
def test_tc002_05_text_primary_keys_cannot_be_null(migrated_db, statement):
    with pytest.raises(sqlite3.IntegrityError) as error:
        with write_transaction(migrated_db) as connection:
            connection.execute(statement)
    assert error.value.sqlite_errorcode == sqlite3.SQLITE_CONSTRAINT_NOTNULL


@pytest.mark.parametrize("operation", ["insert", "update"])
@pytest.mark.parametrize("status", ["INVALID", None])
def test_tc002_06_invalid_status_rejected(migrated_db, operation, status):
    with write_transaction(migrated_db) as connection:
        insert_reservation(connection)
    with closing(connect(migrated_db)) as connection:
        before = snapshot(connection)
    with pytest.raises(sqlite3.IntegrityError):
        with write_transaction(migrated_db) as connection:
            if operation == "insert":
                insert_reservation(connection, OTHER_RESERVATION_ID, status=status)
            else:
                connection.execute("UPDATE reservations SET status = ? WHERE id = ?", (status, RESERVATION_ID))
    with closing(connect(migrated_db)) as connection:
        assert snapshot(connection) == before


def test_tc002_06_confirmed_and_cancelled_insert_and_update(migrated_db):
    with write_transaction(migrated_db) as connection:
        insert_reservation(connection)
        insert_reservation(connection, OTHER_RESERVATION_ID, status="CANCELLED")
    with write_transaction(migrated_db) as connection:
        connection.execute("UPDATE reservations SET status = 'CANCELLED' WHERE id = ?", (RESERVATION_ID,))
        connection.execute("UPDATE reservations SET status = 'CONFIRMED' WHERE id = ?", (OTHER_RESERVATION_ID,))
    with closing(connect(migrated_db)) as connection:
        assert [tuple(row) for row in connection.execute("SELECT id, status FROM reservations ORDER BY id")] == [
            (RESERVATION_ID, "CANCELLED"), (OTHER_RESERVATION_ID, "CONFIRMED"),
        ]
        assert_db_invariants(connection)


def test_tc002_07_pragmas_are_set_on_each_independent_connection(migrated_db):
    with closing(connect(migrated_db, busy_timeout_ms=83)) as first:
        with closing(connect(migrated_db, busy_timeout_ms=177)) as second:
            assert first is not second
            for connection, timeout in [(first, 83), (second, 177)]:
                assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
                assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
                assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == timeout
                assert connection.isolation_level is None
                assert connection.in_transaction is False
                row = connection.execute("SELECT 1 AS value").fetchone()
                assert isinstance(row, sqlite3.Row)
                assert row["value"] == 1


@pytest.mark.parametrize("timeout", [0, 1, 1001, 1003, 5000, 2_147_483_647])
def test_tc002_07_valid_timeout_boundaries(migrated_db, timeout):
    with closing(connect(migrated_db, busy_timeout_ms=timeout)) as connection:
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == timeout


@pytest.mark.parametrize("timeout", [-1, True, False, 1.5, "100", None, 2_147_483_648])
def test_tc002_07_invalid_timeout_rejected_before_open(tmp_path, timeout):
    path = tmp_path / "missing-parent" / "database.db"
    with pytest.raises(ValueError, match="busy_timeout_ms"):
        connect(path, busy_timeout_ms=timeout)
    assert not path.parent.exists()


class InterruptedWrite(BaseException):
    """Exercise cancellation/interrupt cleanup without interrupting pytest itself."""


@pytest.mark.parametrize("exception", [RuntimeError, InterruptedWrite])
def test_tc002_08_exception_rolls_back_all_rows_and_closes(migrated_db, exception):
    with closing(connect(migrated_db)) as reader:
        before = snapshot(reader)
    with pytest.raises(exception):
        with write_transaction(migrated_db) as connection:
            assert connection.in_transaction
            insert_reservation(connection)
            connection.execute(
                "INSERT INTO reserved_dates (date, reservation_id, type) VALUES (?, ?, ?)",
                ("2026-12-02", RESERVATION_ID, "RESERVED"),
            )
            connection.execute(
                "INSERT INTO reservation_logs (reservation_id, action) VALUES (?, ?)",
                (RESERVATION_ID, "CREATE"),
            )
            connection.execute("UPDATE settings SET value = ? WHERE key = ?", ("30", "booking_horizon_days"))
            raise exception("synthetic failure after all writes")
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")
    with closing(connect(migrated_db)) as reader:
        assert snapshot(reader) == before
        for table in ("reservations", "reserved_dates", "reservation_logs"):
            assert reader.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert_db_invariants(reader)
    with write_transaction(migrated_db, busy_timeout_ms=100) as next_writer:
        insert_reservation(next_writer)


def test_tc002_08_failed_commit_rolls_back_and_closes(migrated_db):
    with closing(connect(migrated_db)) as reader:
        before = snapshot(reader)
    with pytest.raises(sqlite3.IntegrityError) as error:
        with write_transaction(migrated_db) as connection:
            connection.execute("PRAGMA defer_foreign_keys = ON")
            insert_reservation(connection)
            connection.execute(
                "INSERT INTO reservation_logs (reservation_id, action) VALUES (?, ?)",
                (OTHER_RESERVATION_ID, "CREATE"),
            )
            # This succeeds until COMMIT, which must fail and roll back both rows.
            assert connection.execute("SELECT COUNT(*) FROM reservation_logs").fetchone()[0] == 1
    assert error.value.sqlite_errorcode == sqlite3.SQLITE_CONSTRAINT_FOREIGNKEY
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")
    with closing(connect(migrated_db)) as reader:
        assert snapshot(reader) == before
        assert_db_invariants(reader)
    with write_transaction(migrated_db, busy_timeout_ms=100) as next_writer:
        insert_reservation(next_writer)


def test_tc002_09_writer_timeout_and_success_after_rollback(migrated_db):
    """Writer B really runs while A holds a write lock; all waits are bounded."""
    start = threading.Barrier(2, timeout=3)
    retry = threading.Event()
    outcomes = Queue()

    def competing_writer():
        try:
            start.wait()
            started = time.monotonic()
            try:
                with write_transaction(migrated_db, busy_timeout_ms=80):
                    raise AssertionError("competing writer unexpectedly acquired the lock")
            except sqlite3.OperationalError as error:
                outcomes.put(("busy", error.sqlite_errorcode, time.monotonic() - started))
            if not retry.wait(3):
                raise AssertionError("writer A did not release the lock")
            with write_transaction(migrated_db, busy_timeout_ms=100) as connection:
                insert_reservation(connection, OTHER_RESERVATION_ID)
            outcomes.put(("committed",))
        except BaseException as error:
            outcomes.put(("worker-error", error))

    worker = threading.Thread(target=competing_writer, daemon=True)
    with closing(connect(migrated_db)) as first:
        first.execute("BEGIN IMMEDIATE")
        insert_reservation(first)
        worker.start()
        try:
            start.wait()
            result = outcomes.get(timeout=3)
            assert result[0] == "busy", result
            assert result[1] & 0xFF == sqlite3.SQLITE_BUSY
            assert 0.04 <= result[2] < 3
            assert first.in_transaction
            first.rollback()
            retry.set()
            result = outcomes.get(timeout=3)
            assert result == ("committed",), result
        finally:
            first.rollback()
            retry.set()
            worker.join(timeout=5)
        assert not worker.is_alive()
    with closing(connect(migrated_db)) as reader:
        assert [row[0] for row in reader.execute("SELECT id FROM reservations")] == [OTHER_RESERVATION_ID]
        assert_db_invariants(reader)


def test_tc002_10_success_and_exception_cleanup_preserve_integrity(migrated_db):
    for number in range(5):
        with write_transaction(migrated_db) as connection:
            connection.execute(
                "INSERT INTO reserved_dates (date, type) VALUES (?, ?)",
                (f"2026-12-{number + 2:02}", "BLOCKED"),
            )
        with pytest.raises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")
        with closing(connect(migrated_db)) as reader:
            before = snapshot(reader)
        with pytest.raises(RuntimeError):
            with write_transaction(migrated_db) as failed:
                failed.execute("DELETE FROM reserved_dates")
                raise RuntimeError("synthetic failure")
        with pytest.raises(sqlite3.ProgrammingError):
            failed.execute("SELECT 1")
        with closing(connect(migrated_db)) as reader:
            assert snapshot(reader) == before
            assert_db_invariants(reader)
    with write_transaction(migrated_db) as connection:
        insert_reservation(connection)
    with closing(connect(migrated_db)) as reader:
        assert reader.execute("SELECT COUNT(*) FROM reserved_dates").fetchone()[0] == 5
        assert reader.execute("SELECT COUNT(*) FROM reservations").fetchone()[0] == 1
        assert_db_invariants(reader)


def test_tc002_10_failed_begin_closes_owned_connection(migrated_db, monkeypatch):
    connections = []

    def tracked_connect(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connections.append(connection)
        return connection

    with closing(connect(migrated_db)) as first:
        first.execute("BEGIN IMMEDIATE")
        monkeypatch.setattr(db, "connect", tracked_connect)
        with pytest.raises(sqlite3.OperationalError) as error:
            with write_transaction(migrated_db, busy_timeout_ms=20):
                pytest.fail("locked transaction body must not run")
        assert error.value.sqlite_errorcode & 0xFF == sqlite3.SQLITE_BUSY
        assert len(connections) == 1
        with pytest.raises(sqlite3.ProgrammingError):
            connections[0].execute("SELECT 1")
        first.rollback()
    with write_transaction(migrated_db) as connection:
        insert_reservation(connection)


def test_tc002_10_connection_initialization_failure_closes(monkeypatch):
    connections = []
    sqlite_connect = sqlite3.connect

    def tracked_sqlite_connect(*args, **kwargs):
        connection = sqlite_connect(*args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(db.sqlite3, "connect", tracked_sqlite_connect)
    # SQLite memory databases really reject WAL mode; no simulated DB needed.
    with pytest.raises(db.DatabaseConfigurationError):
        connect(":memory:")
    assert len(connections) == 1
    with pytest.raises(sqlite3.ProgrammingError):
        connections[0].execute("SELECT 1")


def test_tc002_10_invariant_helper_detects_cancelled_occupancy(migrated_db):
    with write_transaction(migrated_db) as connection:
        insert_reservation(connection, status="CANCELLED")
        connection.execute(
            "INSERT INTO reserved_dates (date, reservation_id, type) VALUES (?, ?, ?)",
            ("2026-12-02", RESERVATION_ID, "RESERVED"),
        )
    # FK/CHECK cannot express this cross-table service rule. Future services use
    # the helper to catch violations; RS-002 does not implement those services.
    with closing(connect(migrated_db)) as reader:
        with pytest.raises(AssertionError):
            assert_db_invariants(reader)
