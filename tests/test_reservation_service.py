"""RS-003 date policies and real SQLite service acceptance tests."""

from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import base64
import hashlib
import json
import logging
import os
import queue
import re
import sqlite3
import threading
import time
from uuid import UUID

import pytest

import app.db as db
from app.db import connect, write_transaction
from app.services import reservations
from app.services.calendar import get_calendar
from app.services.date_policy import load_booking_window, month_dates, parse_date, parse_dates
from app.services.errors import (
    DateConflictError,
    InvalidInputError,
    InvalidPolicyError,
    TemporaryDatabaseError,
)
from app.services.reservations import create_reservation
from tests.db_helpers import assert_db_invariants, insert_reservation, snapshot


NOW = datetime(2026, 11, 30, 15, tzinfo=timezone.utc)


@pytest.fixture
def clock():
    return lambda: NOW


def db_snapshot(db_path):
    with closing(connect(db_path)) as connection:
        assert_db_invariants(connection)
        return snapshot(connection)


def reserve(db_path, clock, **overrides):
    inputs = {"name": "QA_USER_A", "memo": "QA_MEMO_SENTINEL", "dates": ["2026-12-02"]}
    inputs.update(overrides)
    return create_reservation(db_path, clock=clock, **inputs)


@pytest.mark.parametrize(
    ("value", "allowed"),
    [("2026-11-30", False), ("2026-12-01", True), ("2027-02-28", True), ("2027-03-01", False)],
)
def test_tc003_01_inclusive_ninety_day_boundaries(migrated_db, clock, value, allowed):
    before = db_snapshot(migrated_db)
    with closing(connect(migrated_db)) as connection:
        connection.execute("BEGIN")
        window = load_booking_window(connection, clock)
        assert window.today == date(2026, 12, 1)
        assert window.max_date == date(2027, 2, 28)
        assert window.horizon_days == 90
        if allowed:
            window.validate(parse_dates([value]))
        else:
            with pytest.raises(InvalidInputError):
                window.validate(parse_dates([value]))
    if allowed:
        assert reserve(migrated_db, clock, dates=[value])["dates"] == [value]
    else:
        with pytest.raises(InvalidInputError):
            reserve(migrated_db, clock, dates=[value])
        assert db_snapshot(migrated_db) == before


@pytest.mark.parametrize("system_tz", ["UTC", "America/Los_Angeles", "Asia/Seoul"])
def test_tc003_02_kst_midnight_ignores_system_timezone(migrated_db, monkeypatch, system_tz):
    original_tz = os.environ.get("TZ")
    monkeypatch.setenv("TZ", system_tz)
    time.tzset()
    try:
        for instant, today, maximum in [
            (NOW - timedelta(seconds=1), "2026-11-30", "2027-02-27"),
            (NOW, "2026-12-01", "2027-02-28"),
        ]:
            result = get_calendar(migrated_db, "2026-12", clock=lambda: instant)
            assert result["today"] == result["min_date"] == today
            assert result["max_date"] == maximum
    finally:
        if original_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original_tz
        time.tzset()


def test_tc003_02_naive_clock_is_rejected_without_writes(migrated_db):
    before = db_snapshot(migrated_db)
    with pytest.raises(ValueError):
        reserve(migrated_db, lambda: datetime(2026, 12, 1))
    assert db_snapshot(migrated_db) == before


@pytest.mark.parametrize("value", ["2028-02-29", "2026-12-01", "0001-01-01", "9999-12-31"])
def test_tc003_03_exact_date_parser_accepts_real_dates(value):
    assert parse_date(value) == date.fromisoformat(value)


@pytest.mark.parametrize("value", [
    "2027-02-29", "2026-02-30", "2026-1-01", "2026-12-1", "2026-12-01T00:00:00",
    "20261201", "2026-W49-2", "2026-12-01\n", " 2026-12-01", "２０２６-12-01",
    "0000-01-01", "2026-00-01", "2026-13-01", "2026-12-00", None, 20261201, True,
])
def test_tc003_03_exact_date_parser_rejects_invalid_input(value):
    with pytest.raises(InvalidInputError):
        parse_date(value)


@pytest.mark.parametrize(("month", "count"), [("2028-02", 29), ("2027-02", 28), ("2026-12", 31), ("2027-01", 31)])
def test_tc003_04_month_lengths_and_year_transition(migrated_db, clock, month, count):
    result = get_calendar(migrated_db, month, clock=clock)
    expected = [f"{month}-{day:02d}" for day in range(1, count + 1)]
    assert [item["date"] for item in result["dates"]] == expected
    assert [item.isoformat() for item in month_dates(month)] == expected
    assert all(set(item) == {"date", "status"} for item in result["dates"])
    assert set(result) == {"dates", "today", "min_date", "max_date"}


@pytest.mark.parametrize("month", ["2026-13", "2026-00", "2026-1", "202612", "2026-12-01", "0000-01", "２０２６-12", "2026-12\n", None, 202612])
def test_tc003_04_invalid_month_is_rejected(migrated_db, clock, month):
    before = db_snapshot(migrated_db)
    with pytest.raises(InvalidInputError):
        get_calendar(migrated_db, month, clock=clock)
    assert db_snapshot(migrated_db) == before


@pytest.mark.parametrize(("name", "memo"), [("가", ""), ("가" * 40, "메" * 200), (" \tQA_USER_A\n", " memo ")])
def test_tc003_05_valid_input_boundaries_and_name_normalization(migrated_db, clock, name, memo):
    result = reserve(migrated_db, clock, name=name, memo=memo)
    with closing(connect(migrated_db)) as connection:
        row = connection.execute("SELECT name, memo FROM reservations WHERE id = ?", (result["id"],)).fetchone()
        assert tuple(row) == (name.strip(), memo)
        assert_db_invariants(connection)


@pytest.mark.parametrize("overrides", [
    {"name": ""}, {"name": " \t\n\u3000"}, {"name": "가" * 41}, {"name": None}, {"name": 123},
    {"memo": "메" * 201}, {"memo": None}, {"memo": []},
    {"dates": []}, {"dates": ["2026-12-02", "2026-12-02"]}, {"dates": ["2026-11-30"]},
    {"dates": ["2027-03-01"]}, {"dates": ["2027-02-29"]}, {"dates": [None]},
    {"dates": "2026-12-02"}, {"dates": None}, {"dates": ("2026-12-02",)},
    {"dates": [(date(2026, 12, 1) + timedelta(days=i)).isoformat() for i in range(91)]},
])
def test_tc003_05_invalid_inputs_leave_entire_database_unchanged(migrated_db, clock, overrides):
    before = db_snapshot(migrated_db)
    with pytest.raises(InvalidInputError) as error:
        reserve(migrated_db, clock, **overrides)
    assert error.value.field in overrides
    assert "QA_USER_A" not in str(error.value)
    assert "QA_MEMO_SENTINEL" not in str(error.value)
    assert db_snapshot(migrated_db) == before


def test_tc003_05_omitted_memo_and_sql_literals_are_stored_as_data(migrated_db, clock):
    name = "O'Neil'); DROP TABLE reservations;--"
    result = create_reservation(migrated_db, name=name, dates=["2026-12-02"], clock=clock)
    with closing(connect(migrated_db)) as connection:
        assert tuple(connection.execute("SELECT name, memo FROM reservations WHERE id = ?", (result["id"],)).fetchone()) == (name, "")
        assert_db_invariants(connection)


def test_tc003_05_full_current_horizon_is_allowed(migrated_db, clock):
    dates = [(date(2026, 12, 1) + timedelta(days=i)).isoformat() for i in range(90)]
    result = reserve(migrated_db, clock, dates=list(reversed(dates)))
    assert result["dates"] == dates
    assert len(db_snapshot(migrated_db)["reserved_dates"]) == 90


@pytest.mark.parametrize("dates", [["2026-12-02"], ["2026-12-02", "2026-12-03"], ["2027-01-01", "2026-12-05", "2026-12-02"]])
def test_tc003_06_atomic_creation_and_utc_audit(migrated_db, clock, dates):
    result = reserve(migrated_db, clock, dates=dates)
    assert set(result) == {"id", "dates", "status", "cancel_token"}
    assert result["dates"] == sorted(dates)
    assert result["status"] == "CONFIRMED"
    assert str(UUID(result["id"])) == result["id"]
    with closing(connect(migrated_db)) as connection:
        row = connection.execute("SELECT * FROM reservations").fetchone()
        assert connection.execute("SELECT count(*) FROM reservations").fetchone()[0] == 1
        assert row["id"] == result["id"]
        assert row["status"] == "CONFIRMED" and row["cancelled_at"] is None
        assert datetime.fromisoformat(row["created_at"]) == NOW
        assert datetime.fromisoformat(row["created_at"]).utcoffset() == timedelta(0)
        occupied = connection.execute("SELECT date, reservation_id, type FROM reserved_dates ORDER BY date").fetchall()
        assert [tuple(row) for row in occupied] == [(value, result["id"], "RESERVED") for value in sorted(dates)]
        logs = connection.execute("SELECT * FROM reservation_logs").fetchall()
        assert len(logs) == 1
        assert logs[0]["reservation_id"] == result["id"]
        assert logs[0]["action"] == "CREATE"
        assert json.loads(logs[0]["detail"]) == {"before": [], "after": sorted(dates)}
        assert datetime.fromisoformat(logs[0]["created_at"]) == NOW
        assert_db_invariants(connection)


def test_tc003_07_calendar_status_priority_and_no_private_fields(migrated_db, clock):
    with write_transaction(migrated_db) as connection:
        reservation_id = insert_reservation(connection)
        for value in ["2026-12-01", "2026-12-04"]:
            connection.execute("INSERT INTO reserved_dates(date,reservation_id,type) VALUES (?,?,'RESERVED')", (value, reservation_id))
        for value in ["2026-12-02", "2026-12-05"]:
            connection.execute("INSERT INTO reserved_dates(date,type,memo) VALUES (?,'BLOCKED',?)", (value, "QA_BLOCK_MEMO"))
        connection.execute("UPDATE settings SET value = '3' WHERE key = 'booking_horizon_days'")
    before = db_snapshot(migrated_db)
    result = get_calendar(migrated_db, "2026-12", clock=lambda: NOW + timedelta(days=2))
    states = {item["date"]: item["status"] for item in result["dates"]}
    assert states["2026-12-01"] == states["2026-12-02"] == "PAST"
    assert states["2026-12-03"] == states["2026-12-06"] == "AVAILABLE"
    assert states["2026-12-04"] == "RESERVED"
    assert states["2026-12-05"] == "BLOCKED"
    assert result["today"] == result["min_date"] == "2026-12-03"
    assert result["max_date"] == "2026-12-05"
    assert set(result) == {"dates", "today", "min_date", "max_date"}
    assert all(set(item) == {"date", "status"} for item in result["dates"])
    serialized = json.dumps(result)
    for private in [reservation_id, "QA_USER_A", "QA_MEMO_SENTINEL", "QA_BLOCK_MEMO", "cancel_token", "name", "memo"]:
        assert private not in serialized
    assert db_snapshot(migrated_db) == before


def test_tc003_07_calendar_policy_and_occupancy_share_read_snapshot(migrated_db, clock):
    def concurrent_writer_clock():
        # The policy SELECT has started the reader's WAL snapshot. A second
        # connection can commit while the reader retains that original view.
        with write_transaction(migrated_db) as writer:
            writer.execute("UPDATE settings SET value='7' WHERE key='booking_horizon_days'")
            writer.execute("INSERT INTO reserved_dates(date,type) VALUES ('2026-12-02','BLOCKED')")
        return NOW

    first = get_calendar(migrated_db, "2026-12", clock=concurrent_writer_clock)
    assert first["max_date"] == "2027-02-28"
    assert first["dates"][1] == {"date": "2026-12-02", "status": "AVAILABLE"}
    latest = get_calendar(migrated_db, "2026-12", clock=clock)
    assert latest["max_date"] == "2026-12-07"
    assert latest["dates"][1] == {"date": "2026-12-02", "status": "BLOCKED"}
    assert db_snapshot(migrated_db)["reservations"] == []


@pytest.mark.parametrize("occupancy_type", ["RESERVED", "BLOCKED"])
def test_tc003_10_partial_conflict_leaves_all_tables_unchanged(migrated_db, clock, occupancy_type):
    if occupancy_type == "RESERVED":
        reserve(migrated_db, clock, dates=["2026-12-03"])
    else:
        with write_transaction(migrated_db) as connection:
            connection.execute("INSERT INTO reserved_dates(date,type,memo) VALUES ('2026-12-03','BLOCKED','QA_BLOCK_MEMO')")
    before = db_snapshot(migrated_db)
    with pytest.raises(DateConflictError) as error:
        reserve(migrated_db, clock, dates=["2026-12-04", "2026-12-03", "2026-12-02"])
    assert error.value.dates == ("2026-12-03",)
    assert not any(value in str(error.value) for value in ["QA_USER_A", "QA_MEMO_SENTINEL", "QA_BLOCK_MEMO"])
    assert db_snapshot(migrated_db) == before


def test_tc003_10_date_primary_key_remains_final_conflict_defense(migrated_db, clock, monkeypatch):
    reserve(migrated_db, clock, dates=["2026-12-03"])
    before = db_snapshot(migrated_db)
    monkeypatch.setattr(reservations, "find_conflicts", lambda *args: ())
    with pytest.raises(DateConflictError) as error:
        reserve(migrated_db, clock, dates=["2026-12-02", "2026-12-03", "2026-12-04"])
    assert error.value.dates == ("2026-12-03",)
    assert db_snapshot(migrated_db) == before


@pytest.mark.parametrize("failure_point", ["second_date", "log"])
def test_tc003_11_mid_write_sql_failure_rolls_back_everything(migrated_db, clock, failure_point):
    with write_transaction(migrated_db) as connection:
        if failure_point == "second_date":
            connection.execute("CREATE TRIGGER fail_write BEFORE INSERT ON reserved_dates WHEN NEW.date='2026-12-03' BEGIN SELECT RAISE(ABORT, 'QA_FAILURE'); END")
        else:
            connection.execute("CREATE TRIGGER fail_write BEFORE INSERT ON reservation_logs BEGIN SELECT RAISE(ABORT, 'QA_FAILURE'); END")
    before = db_snapshot(migrated_db)
    with pytest.raises(sqlite3.IntegrityError) as error:
        reserve(migrated_db, clock, dates=["2026-12-02", "2026-12-03", "2026-12-04"])
    assert error.value.sqlite_errorcode == sqlite3.SQLITE_CONSTRAINT_TRIGGER
    assert db_snapshot(migrated_db) == before
    with write_transaction(migrated_db) as connection:
        connection.execute("DROP TRIGGER fail_write")
    assert reserve(migrated_db, clock)["status"] == "CONFIRMED"


def test_tc003_11_python_failure_after_log_write_rolls_back(migrated_db, clock, monkeypatch):
    original = reservations.append_creation_log
    before = db_snapshot(migrated_db)

    def fail_after_log(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("QA_FAILURE_AFTER_LOG")

    monkeypatch.setattr(reservations, "append_creation_log", fail_after_log)
    with pytest.raises(RuntimeError, match="QA_FAILURE_AFTER_LOG"):
        reserve(migrated_db, clock)
    assert db_snapshot(migrated_db) == before


def test_tc003_11_commit_failure_returns_no_result_and_rolls_back(migrated_db, clock, monkeypatch):
    original = reservations.append_creation_log
    before = db_snapshot(migrated_db)

    def violate_deferred_fk(connection, *args):
        original(connection, *args)
        connection.execute("PRAGMA defer_foreign_keys = ON")
        connection.execute(
            "INSERT INTO reserved_dates(date,reservation_id,type) VALUES (?,?,'RESERVED')",
            ("2099-01-01", "99999999-9999-4999-8999-999999999999"),
        )
        assert connection.execute("SELECT count(*) FROM reservation_logs").fetchone()[0] == 1

    monkeypatch.setattr(reservations, "append_creation_log", violate_deferred_fk)
    with pytest.raises(sqlite3.IntegrityError) as error:
        reserve(migrated_db, clock)
    assert error.value.sqlite_errorcode == sqlite3.SQLITE_CONSTRAINT_FOREIGNKEY
    assert db_snapshot(migrated_db) == before


def test_tc003_12_real_writer_busy_is_temporary_and_releases_connection(migrated_db, clock):
    before = db_snapshot(migrated_db)
    with closing(connect(migrated_db)) as writer:
        writer.execute("BEGIN IMMEDIATE")
        try:
            started = time.monotonic()
            with pytest.raises(TemporaryDatabaseError):
                reserve(migrated_db, clock, busy_timeout_ms=40)
            assert time.monotonic() - started < 2
        finally:
            writer.rollback()
    assert db_snapshot(migrated_db) == before
    assert reserve(migrated_db, clock)["status"] == "CONFIRMED"


def test_tc003_12_real_sqlite_locked_is_temporary_and_atomic(migrated_db, clock, monkeypatch):
    original = reservations.append_creation_log
    before = db_snapshot(migrated_db)
    sqlite_codes = []

    def locked_table(connection, *args):
        original(connection, *args)
        cursor = connection.execute("SELECT * FROM reserved_dates")
        cursor.fetchone()  # Leave a live row cursor on the table being dropped.
        try:
            connection.execute("DROP TABLE reserved_dates")
        except sqlite3.OperationalError as error:
            sqlite_codes.append(error.sqlite_errorcode)
            raise
        finally:
            cursor.close()

    monkeypatch.setattr(reservations, "append_creation_log", locked_table)
    with pytest.raises(TemporaryDatabaseError):
        reserve(migrated_db, clock, dates=["2026-12-02", "2026-12-03"])
    assert sqlite_codes == [sqlite3.SQLITE_LOCKED]
    assert db_snapshot(migrated_db) == before


def test_tc003_12_unexpected_sql_error_is_not_conflict_or_busy(migrated_db, clock):
    with write_transaction(migrated_db) as connection:
        connection.execute("CREATE TRIGGER fail_sql BEFORE INSERT ON reservation_logs BEGIN SELECT * FROM qa_missing_table; END")
    before = db_snapshot(migrated_db)
    with pytest.raises(sqlite3.OperationalError) as error:
        reserve(migrated_db, clock)
    assert error.value.sqlite_errorcode == sqlite3.SQLITE_ERROR
    assert db_snapshot(migrated_db) == before


def test_tc003_12_other_primary_key_violation_is_not_date_conflict(migrated_db, clock):
    with write_transaction(migrated_db) as connection:
        connection.execute("CREATE TRIGGER fail_pk BEFORE INSERT ON reserved_dates BEGIN INSERT INTO settings(key,value) VALUES ('booking_horizon_days','90'); END")
    before = db_snapshot(migrated_db)
    with pytest.raises(sqlite3.IntegrityError) as error:
        reserve(migrated_db, clock)
    assert error.value.sqlite_errorcode == sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY
    assert db_snapshot(migrated_db) == before


def test_tc003_12_uuid_collision_is_not_date_conflict(migrated_db, clock, monkeypatch):
    existing = reserve(migrated_db, clock)
    before = db_snapshot(migrated_db)
    monkeypatch.setattr(reservations, "uuid4", lambda: UUID(existing["id"]))
    with pytest.raises(sqlite3.IntegrityError) as error:
        reserve(migrated_db, clock, dates=["2026-12-03"])
    assert error.value.sqlite_errorcode == sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY
    assert db_snapshot(migrated_db) == before


def test_tc003_13_random_tokens_only_hashes_persist_and_no_logging(migrated_db, clock, caplog):
    caplog.set_level(logging.DEBUG)
    results = [reserve(migrated_db, clock, dates=[value]) for value in ["2026-12-02", "2026-12-03"]]
    tokens = [result["cancel_token"] for result in results]
    assert tokens[0] != tokens[1]
    for token in tokens:
        assert re.fullmatch(r"[A-Za-z0-9_-]+", token)
        assert len(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))) >= 32
    with closing(connect(migrated_db)) as connection:
        dump = "\n".join(connection.iterdump())
        logs = repr([tuple(row) for row in connection.execute("SELECT * FROM reservation_logs")])
        for result in results:
            token_hash = hashlib.sha256(result["cancel_token"].encode("ascii")).hexdigest()
            stored = connection.execute("SELECT cancel_token_hash FROM reservations WHERE id = ?", (result["id"],)).fetchone()[0]
            assert stored == token_hash
            assert token_hash not in logs
            assert token_hash not in caplog.text
            assert result["cancel_token"] not in dump
            assert result["cancel_token"] not in caplog.text
        for private in ["QA_USER_A", "QA_MEMO_SENTINEL"]:
            assert private not in logs
            assert private not in caplog.text
        assert_db_invariants(connection)
    calendar = json.dumps(get_calendar(migrated_db, "2026-12", clock=clock))
    for private in tokens + [result["id"] for result in results] + ["QA_USER_A", "QA_MEMO_SENTINEL"]:
        assert private not in calendar


def test_tc003_14_current_db_horizon_changes_without_deleting_existing_occupancy(migrated_db, clock):
    existing = reserve(migrated_db, clock, dates=["2027-02-28"])
    with write_transaction(migrated_db) as connection:
        connection.execute("UPDATE settings SET value='7' WHERE key='booking_horizon_days'")
    result = get_calendar(migrated_db, "2027-02", clock=clock)
    assert result["max_date"] == "2026-12-07"
    assert result["dates"][-1] == {"date": "2027-02-28", "status": "RESERVED"}
    assert reserve(migrated_db, clock, dates=["2026-12-07"])["dates"] == ["2026-12-07"]
    before = db_snapshot(migrated_db)
    with pytest.raises(InvalidInputError):
        reserve(migrated_db, clock, dates=["2026-12-08"])
    assert db_snapshot(migrated_db) == before
    assert any(row[0] == existing["id"] for row in before["reservations"])


@pytest.mark.parametrize("change", ["horizon", "midnight"])
def test_tc003_14_waiting_writer_uses_policy_and_clock_after_lock(migrated_db, monkeypatch, change):
    entered_begin = threading.Event()
    outcomes = queue.Queue()
    current_time = [NOW]
    clock_calls = []
    original_connect = db.connect

    def observed_connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        connection.set_trace_callback(lambda sql: entered_begin.set() if sql == "BEGIN IMMEDIATE" else None)
        return connection

    def observed_clock():
        clock_calls.append(current_time[0])
        return current_time[0]

    def worker():
        try:
            value = "2026-12-08" if change == "horizon" else "2026-12-01"
            outcomes.put(reserve(migrated_db, observed_clock, dates=[value], busy_timeout_ms=3000))
        except BaseException as exc:
            outcomes.put(exc)

    with closing(original_connect(migrated_db)) as writer:
        writer.execute("BEGIN IMMEDIATE")
        monkeypatch.setattr(db, "connect", observed_connect)
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        try:
            assert entered_begin.wait(2), "worker never attempted BEGIN IMMEDIATE"
            assert not clock_calls, "clock was evaluated before writer lock"
            if change == "horizon":
                writer.execute("UPDATE settings SET value='7' WHERE key='booking_horizon_days'")
            else:
                current_time[0] = NOW + timedelta(days=1)
            writer.commit()
        finally:
            writer.rollback()
            thread.join(5)
        assert not thread.is_alive(), "worker did not exit"
    outcome = outcomes.get_nowait()
    assert isinstance(outcome, InvalidInputError)
    assert clock_calls == [current_time[0]]
    current = db_snapshot(migrated_db)
    assert current["reservations"] == current["reserved_dates"] == current["reservation_logs"] == []


@pytest.mark.parametrize("value", [None, "0", "-1", "abc", "1.5", "", "999999999999999999999999"])
def test_tc003_14_invalid_or_missing_database_policy_fails_closed(migrated_db, clock, value):
    with write_transaction(migrated_db) as connection:
        if value is None:
            connection.execute("DELETE FROM settings WHERE key='booking_horizon_days'")
        else:
            connection.execute("UPDATE settings SET value=? WHERE key='booking_horizon_days'", (value,))
    before = db_snapshot(migrated_db)
    with pytest.raises(InvalidPolicyError):
        reserve(migrated_db, clock)
    with pytest.raises(InvalidPolicyError):
        get_calendar(migrated_db, "2026-12", clock=clock)
    assert db_snapshot(migrated_db) == before
