"""RS-004 cancellation policies and atomicity against isolated SQLite files."""

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from queue import Queue
import sqlite3
import threading
import time

import pytest

from app import db
from app.services import cancellation
from app.services.cancellation import (
    CancellationNotAllowedError, InvalidCancellationCredentialsError, cancel_reservation,
)
from app.services.errors import TemporaryDatabaseError
from app.services.reservations import create_reservation
from app.services.tokens import hash_token
from tests.db_helpers import assert_db_invariants, snapshot


NOW = datetime(2026, 11, 30, 15, tzinfo=timezone.utc)
DATES = ["2026-12-02", "2026-12-04"]


def _create(db_path, *, dates=None, now=NOW):
    return create_reservation(
        db_path, name="QA_USER_A", memo="QA_MEMO_SENTINEL",
        dates=DATES if dates is None else dates, clock=lambda: now,
    )


def _cancel(db_path, reservation, *, now=NOW, **kwargs):
    return cancel_reservation(
        db_path, id=reservation["id"], cancel_token=reservation["cancel_token"],
        clock=lambda: now, **kwargs,
    )


def _snapshot(db_path):
    with closing(db.connect(db_path)) as connection:
        return snapshot(connection)


def test_tc004_05_cancel_and_rebook_preserves_audit(migrated_db):
    reservation = _create(migrated_db)
    result = _cancel(migrated_db, reservation)
    assert result == {"id": reservation["id"], "status": "CANCELLED"}
    with closing(db.connect(migrated_db)) as connection:
        row = connection.execute("SELECT * FROM reservations").fetchone()
        assert row["status"] == "CANCELLED"
        assert row["cancelled_at"] == NOW.isoformat()
        assert datetime.fromisoformat(row["cancelled_at"]).utcoffset() == timedelta(0)
        assert row["name"] == "QA_USER_A"
        assert row["memo"] == "QA_MEMO_SENTINEL"
        assert connection.execute("SELECT * FROM reserved_dates").fetchall() == []
        logs = connection.execute("SELECT * FROM reservation_logs ORDER BY id").fetchall()
        assert [row["action"] for row in logs] == ["CREATE", "CANCEL"]
        assert logs[1]["reservation_id"] == reservation["id"]
        assert logs[1]["created_at"] == NOW.isoformat()
        assert json.loads(logs[1]["detail"]) == {"before": DATES, "after": []}
        assert_db_invariants(connection)
    replacement = _create(migrated_db)
    assert replacement["id"] != reservation["id"]
    before_repeat = _snapshot(migrated_db)
    assert _cancel(migrated_db, reservation) == result
    assert _snapshot(migrated_db) == before_repeat


@pytest.mark.parametrize("kind", ["wrong-token", "absent-id", "malformed-id", "surrogate-id", "empty-token", "unicode-token"])
def test_tc004_06_bad_credentials_have_same_error_and_no_writes(migrated_db, monkeypatch, kind):
    reservation = _create(migrated_db)
    values = {"id": reservation["id"], "cancel_token": reservation["cancel_token"]}
    if kind == "wrong-token":
        values["cancel_token"] = "x" * 43
    elif kind == "absent-id":
        values["id"] = "11111111-1111-4111-8111-111111111111"
    elif kind == "malformed-id":
        values["id"] = "' OR 1=1 --"
    elif kind == "surrogate-id":
        values["id"] = "\ud800"
    elif kind == "empty-token":
        values["cancel_token"] = ""
    else:
        values["cancel_token"] = "한글"
    before = _snapshot(migrated_db)
    comparisons = []
    real_compare = cancellation.hmac.compare_digest

    def observe_compare(left, right):
        comparisons.append((len(left), len(right)))
        return real_compare(left, right)

    monkeypatch.setattr(cancellation.hmac, "compare_digest", observe_compare)
    with pytest.raises(InvalidCancellationCredentialsError) as failure:
        cancel_reservation(migrated_db, **values, clock=lambda: NOW)
    assert failure.value.code == "CANCELLATION_NOT_FOUND"
    assert str(failure.value) == "Reservation credentials are invalid"
    assert comparisons == [(64, 64)]
    assert _snapshot(migrated_db) == before


@pytest.mark.parametrize("field,value", [("id", None), ("id", 123), ("cancel_token", None), ("cancel_token", False)])
def test_tc004_06_service_rejects_nonstring_credentials(migrated_db, field, value):
    reservation = _create(migrated_db)
    values = {"id": reservation["id"], "cancel_token": reservation["cancel_token"], field: value}
    before = _snapshot(migrated_db)
    with pytest.raises(InvalidCancellationCredentialsError):
        cancel_reservation(migrated_db, **values)
    assert _snapshot(migrated_db) == before


@pytest.mark.parametrize("days_later", [1, 2], ids=["today-and-future", "past-and-future"])
def test_tc004_07_any_nonfuture_date_rejects_entire_cancellation(migrated_db, days_later):
    reservation = _create(migrated_db)
    before = _snapshot(migrated_db)
    with pytest.raises(CancellationNotAllowedError):
        _cancel(migrated_db, reservation, now=NOW + timedelta(days=days_later))
    assert _snapshot(migrated_db) == before


@pytest.mark.parametrize("seconds,allowed", [(-1, True), (0, False)])
def test_tc004_07_kst_midnight_boundary(migrated_db, seconds, allowed):
    reservation = _create(migrated_db)
    before = _snapshot(migrated_db)
    now = NOW + timedelta(days=1, seconds=seconds)
    if allowed:
        assert _cancel(migrated_db, reservation, now=now)["status"] == "CANCELLED"
    else:
        with pytest.raises(CancellationNotAllowedError):
            _cancel(migrated_db, reservation, now=now)
        assert _snapshot(migrated_db) == before


def test_tc004_07_cancellation_ignores_reduced_booking_horizon(migrated_db):
    reservation = _create(migrated_db)
    with db.write_transaction(migrated_db) as connection:
        connection.execute("UPDATE settings SET value = '1' WHERE key = 'booking_horizon_days'")
    assert _cancel(migrated_db, reservation)["status"] == "CANCELLED"


def test_tc004_07_clock_read_after_writer_lock(migrated_db, monkeypatch):
    reservation = _create(migrated_db)
    before = _snapshot(migrated_db)
    real_connect = db.connect
    connected = threading.Event()
    results = Queue()
    clock_calls = []
    now = [NOW]

    def observed_connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        connected.set()
        return connection

    def clock():
        clock_calls.append(now[0])
        return now[0]

    def worker():
        try:
            results.put(cancel_reservation(
                migrated_db, id=reservation["id"], cancel_token=reservation["cancel_token"], clock=clock,
            ))
        except BaseException as error:
            results.put(type(error))

    thread = threading.Thread(target=worker, daemon=True)
    with closing(real_connect(migrated_db)) as writer:
        writer.execute("BEGIN IMMEDIATE")
        with monkeypatch.context() as patch:
            patch.setattr(db, "connect", observed_connect)
            try:
                thread.start()
                assert connected.wait(timeout=5)
                assert clock_calls == []
                now[0] = NOW + timedelta(days=1)
                writer.rollback()
                thread.join(timeout=10)
            finally:
                writer.rollback()
                thread.join(timeout=5)
    assert not thread.is_alive()
    assert results.get_nowait() is CancellationNotAllowedError
    assert clock_calls == [now[0]]
    assert _snapshot(migrated_db) == before


@pytest.mark.parametrize("phase", ["delete", "update", "log", "commit"])
def test_tc004_08_all_cancellation_failures_roll_back(migrated_db, phase):
    reservation = _create(migrated_db)
    with closing(db.connect(migrated_db)) as connection:
        if phase == "commit":
            connection.execute("CREATE TABLE failure_parent(id INTEGER PRIMARY KEY)")
            connection.execute(
                "CREATE TABLE failure_child(id INTEGER REFERENCES failure_parent(id) DEFERRABLE INITIALLY DEFERRED)"
            )
            connection.execute(
                "CREATE TRIGGER fail_cancel AFTER UPDATE OF status ON reservations "
                "BEGIN INSERT INTO failure_child VALUES (1); END"
            )
        else:
            trigger = {
                "delete": "AFTER DELETE ON reserved_dates",
                "update": "AFTER UPDATE OF status ON reservations",
                "log": "BEFORE INSERT ON reservation_logs WHEN NEW.action = 'CANCEL'",
            }[phase]
            connection.execute(
                f"CREATE TRIGGER fail_cancel {trigger} BEGIN SELECT RAISE(ABORT, 'QA_FAILURE'); END"
            )
    before = _snapshot(migrated_db)
    with pytest.raises(sqlite3.IntegrityError):
        _cancel(migrated_db, reservation)
    assert _snapshot(migrated_db) == before
    with closing(db.connect(migrated_db)) as connection:
        assert_db_invariants(connection)
        if phase == "commit":
            assert connection.execute("SELECT * FROM failure_child").fetchall() == []
        connection.execute("DROP TRIGGER fail_cancel")
    # The failed transaction released its lock and can be retried explicitly.
    assert _cancel(migrated_db, reservation)["status"] == "CANCELLED"


def test_tc004_09_repeat_authenticates_without_duplicate_log(migrated_db):
    reservation = _create(migrated_db)
    expected = _cancel(migrated_db, reservation)
    before = _snapshot(migrated_db)
    # A previous cancellation remains a success after its original dates pass.
    assert _cancel(migrated_db, reservation, now=NOW + timedelta(days=10)) == expected
    assert _snapshot(migrated_db) == before
    with pytest.raises(InvalidCancellationCredentialsError):
        cancel_reservation(migrated_db, id=reservation["id"], cancel_token="wrong", clock=lambda: NOW)
    assert _snapshot(migrated_db) == before


@pytest.mark.parametrize("attempt", range(20), ids=lambda value: f"TC-004-09-run-{value + 1:02}")
def test_tc004_09_simultaneous_cancellation_changes_state_once(migrated_db, monkeypatch, attempt):
    reservation = _create(migrated_db)
    real_connect = db.connect
    start = threading.Barrier(3, timeout=5)
    connected = threading.Barrier(2, timeout=5)
    connections = []
    lock = threading.Lock()
    outcomes = Queue()

    def observed_connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        try:
            with lock:
                connections.append(connection)
            connected.wait()
        except BaseException:
            connection.close()
            raise
        return connection

    def worker():
        try:
            start.wait()
            outcomes.put(_cancel(migrated_db, reservation))
        except BaseException as error:
            outcomes.put(type(error).__name__)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(2)]
    with monkeypatch.context() as patch:
        patch.setattr(db, "connect", observed_connect)
        try:
            for thread in threads:
                thread.start()
            start.wait()
            deadline = time.monotonic() + 15
            for thread in threads:
                thread.join(timeout=max(0, deadline - time.monotonic()))
        finally:
            start.abort()
            connected.abort()
            for thread in threads:
                thread.join(timeout=1)
    assert not any(thread.is_alive() for thread in threads)
    assert len(connections) == 2
    assert connections[0] is not connections[1]
    assert outcomes.qsize() == 2
    expected = {"id": reservation["id"], "status": "CANCELLED"}
    assert [outcomes.get_nowait(), outcomes.get_nowait()] == [expected, expected]
    with closing(real_connect(migrated_db)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM reserved_dates").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM reservations WHERE status = 'CANCELLED'").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM reservation_logs WHERE action = 'CANCEL'").fetchone()[0] == 1
        assert_db_invariants(connection)


def test_tc004_10_cancellation_logs_contain_only_dates(migrated_db, caplog):
    reservation = _create(migrated_db)
    result = _cancel(migrated_db, reservation)
    with closing(db.connect(migrated_db)) as connection:
        logs = str([tuple(row) for row in connection.execute("SELECT * FROM reservation_logs")])
        stored_hash = connection.execute("SELECT cancel_token_hash FROM reservations").fetchone()[0]
    assert stored_hash == hash_token(reservation["cancel_token"])
    for secret in [reservation["cancel_token"], stored_hash, "QA_USER_A", "QA_MEMO_SENTINEL"]:
        assert secret not in logs
        assert secret not in str(result)
        assert secret not in caplog.text


def test_tc004_13_cancellation_busy_is_temporary_and_unchanged(migrated_db):
    reservation = _create(migrated_db)
    before = _snapshot(migrated_db)
    with closing(db.connect(migrated_db)) as writer:
        writer.execute("BEGIN IMMEDIATE")
        with pytest.raises(TemporaryDatabaseError):
            _cancel(migrated_db, reservation, busy_timeout_ms=1)
        writer.rollback()
    assert _snapshot(migrated_db) == before
