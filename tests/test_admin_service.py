"""RS-006 management transactions against isolated, real SQLite files."""

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from queue import Queue
import sqlite3
import threading
import time

import pytest

from app import db
from app.services import admin
from app.services.cancellation import cancel_reservation as public_cancel
from app.services.errors import DateConflictError, InvalidInputError, InvalidPolicyError, TemporaryDatabaseError
from app.services.reservations import create_reservation
from tests.db_helpers import assert_db_invariants, snapshot


NOW = datetime(2026, 11, 30, 15, tzinfo=timezone.utc)


def clock():
    return NOW


def state(path):
    with closing(db.connect(path)) as connection:
        assert_db_invariants(connection)
        return snapshot(connection)


def reserve(path, **changes):
    values = {"name": "QA_USER_A", "memo": "QA_MEMO_SENTINEL", "dates": ["2026-12-02", "2026-12-03"], "clock": clock}
    values.update(changes)
    return create_reservation(path, **values)


def test_tc006_04_month_query_returns_all_dates_and_cancelled_snapshots(migrated_db):
    a = reserve(migrated_db, dates=["2026-12-31", "2027-01-02"])
    b = reserve(migrated_db, dates=["2026-12-04"])
    for month in ["2026-12", "2027-01"]:
        rows = admin.list_reservations(migrated_db, month)["reservations"]
        row = next(r for r in rows if r["id"] == a["id"])
        assert row["dates"] == a["dates"]
        assert set(row) == {"id", "dates", "name", "memo", "status", "created_at", "cancelled_at"}
    admin.cancel_reservation(migrated_db, id=a["id"], clock=clock)
    public_cancel(migrated_db, id=b["id"], cancel_token=b["cancel_token"], clock=clock)
    assert admin.list_reservations(migrated_db, "2026-12")["reservations"] == []
    rows = admin.list_reservations(migrated_db, "2026-12", status="CANCELLED")["reservations"]
    assert {r["id"] for r in rows} == {a["id"], b["id"]}
    assert next(r for r in rows if r["id"] == a["id"])["dates"] == a["dates"]
    assert admin.list_reservations(migrated_db, "2027-02", status="ALL")["reservations"] == []
    assert a["cancel_token"] not in json.dumps(rows)


def test_tc006_05_self_dates_and_omitted_fields_preserved(migrated_db):
    a = reserve(migrated_db)
    before = state(migrated_db)
    updated = admin.update_reservation(migrated_db, id=a["id"], changes={"name": "  QA_USER_B  ", "dates": ["2026-12-04", "2026-12-02"]}, clock=clock)
    assert updated["name"] == "QA_USER_B"
    assert updated["memo"] == "QA_MEMO_SENTINEL"
    assert updated["dates"] == ["2026-12-02", "2026-12-04"]
    after = state(migrated_db)
    assert [r[0] for r in after["reserved_dates"]] == updated["dates"]
    assert len(after["reservation_logs"]) == len(before["reservation_logs"]) + 1
    assert after["reservations"][0][3] == before["reservations"][0][3]  # unchanged token hash
    assert json.loads(after["reservation_logs"][-1][3]) == {"before": a["dates"], "after": updated["dates"]}
    again = admin.update_reservation(migrated_db, id=a["id"], changes={"memo": "new"}, clock=clock)
    assert again["dates"] == updated["dates"]
    same = state(migrated_db)
    admin.update_reservation(migrated_db, id=a["id"], changes={"memo": "new"}, clock=clock)
    assert state(migrated_db) == same


@pytest.mark.parametrize("kind", ["RESERVED", "BLOCKED"])
def test_tc006_06_conflict_preserves_entire_original(migrated_db, kind):
    a = reserve(migrated_db)
    if kind == "RESERVED":
        reserve(migrated_db, dates=["2026-12-05"])
    else:
        admin.block_dates(migrated_db, dates=["2026-12-05"], clock=clock)
    before = state(migrated_db)
    with pytest.raises(DateConflictError) as error:
        admin.update_reservation(migrated_db, id=a["id"], changes={"name": "replacement", "memo": "changed", "dates": ["2026-12-02", "2026-12-05"]}, clock=clock)
    assert error.value.dates == ("2026-12-05",)
    assert state(migrated_db) == before


def test_tc006_07_cancellation_is_atomic_idempotent_and_preserves_history(migrated_db):
    a = reserve(migrated_db)
    assert admin.cancel_reservation(migrated_db, id=a["id"], clock=clock) == {"id": a["id"], "status": "CANCELLED"}
    after = state(migrated_db)
    assert after["reserved_dates"] == []
    assert len(after["reservations"]) == 1
    assert len(after["reservation_logs"]) == 2
    row = admin.list_reservations(migrated_db, "2026-12", status="CANCELLED")["reservations"][0]
    assert row["cancelled_at"] == NOW.isoformat()
    assert row["dates"] == a["dates"]
    admin.cancel_reservation(migrated_db, id=a["id"], clock=lambda: NOW + timedelta(days=10))
    assert state(migrated_db) == after
    with pytest.raises(admin.AdminChangeNotAllowedError):
        admin.update_reservation(migrated_db, id=a["id"], changes={"name": "forbidden"}, clock=clock)
    assert state(migrated_db) == after


@pytest.mark.parametrize("action", ["dates", "memo", "name", "cancel"])
def test_tc006_08_past_reservation_changes_denied(migrated_db, action):
    a = reserve(migrated_db, dates=["2026-11-30", "2026-12-02"], clock=lambda: NOW - timedelta(days=1))
    before = state(migrated_db)
    with pytest.raises(admin.AdminChangeNotAllowedError):
        if action == "cancel":
            admin.cancel_reservation(migrated_db, id=a["id"], clock=clock)
        else:
            value = ["2026-12-02"] if action == "dates" else "replacement"
            admin.update_reservation(migrated_db, id=a["id"], changes={action: value}, clock=clock)
    assert state(migrated_db) == before


def test_tc006_08_today_admin_change_and_cancel_allowed(migrated_db):
    a = reserve(migrated_db, dates=["2026-12-01"])
    admin.update_reservation(migrated_db, id=a["id"], changes={"memo": "today"}, clock=clock)
    admin.cancel_reservation(migrated_db, id=a["id"], clock=clock)
    assert state(migrated_db)["reserved_dates"] == []


@pytest.mark.parametrize("kind", ["RESERVED", "BLOCKED"])
def test_tc006_09_batch_block_conflict_rolls_back(migrated_db, kind):
    if kind == "RESERVED":
        reserve(migrated_db, dates=["2026-12-03"])
    else:
        admin.block_dates(migrated_db, dates=["2026-12-03"], clock=clock)
    before = state(migrated_db)
    with pytest.raises(DateConflictError) as error:
        admin.block_dates(migrated_db, dates=["2026-12-02", "2026-12-03", "2026-12-04"], memo="QA_BLOCK_MEMO", clock=clock)
    assert error.value.dates == ("2026-12-03",)
    assert state(migrated_db) == before


def test_tc006_10_unblock_only_blocked_and_keep_past(migrated_db):
    reserve(migrated_db, dates=["2026-12-02"])
    admin.block_dates(migrated_db, dates=["2026-12-03"], clock=clock)
    admin.block_dates(migrated_db, dates=["2026-11-30"], clock=lambda: NOW - timedelta(days=1))
    before = state(migrated_db)
    for day in ["2026-12-02", "2026-12-04"]:
        with pytest.raises(admin.AdminNotFoundError):
            admin.unblock_date(migrated_db, day, clock=clock)
        assert state(migrated_db) == before
    with pytest.raises(admin.AdminChangeNotAllowedError):
        admin.unblock_date(migrated_db, "2026-11-30", clock=clock)
    assert state(migrated_db) == before
    assert admin.unblock_date(migrated_db, "2026-12-03", clock=clock) == {"date": "2026-12-03", "status": "AVAILABLE"}
    after = state(migrated_db)
    assert [r[0] for r in after["reserved_dates"]] == ["2026-11-30", "2026-12-02"]
    assert after["reservations"] == before["reservations"]
    assert len(after["reservation_logs"]) == len(before["reservation_logs"]) + 1


@pytest.mark.parametrize("attempt", range(20))
def test_tc006_11_independent_user_and_admin_writers_one_winner(migrated_db, monkeypatch, attempt):
    actual_connect = db.connect
    start = threading.Barrier(3, timeout=5)
    connected = threading.Barrier(2, timeout=5)
    connections, outcomes = [], Queue()
    lock = threading.Lock()

    def observe(*args, **kwargs):
        connection = actual_connect(*args, **kwargs)
        try:
            with lock:
                connections.append((threading.get_ident(), connection))
            connected.wait()
        except BaseException:
            connection.close()
            raise
        return connection

    def worker(index):
        try:
            start.wait()
            if index == 0:
                reserve(migrated_db, dates=["2026-12-02", "2026-12-03"])
            else:
                admin.block_dates(migrated_db, dates=["2026-12-03", "2026-12-04"], clock=clock)
            outcomes.put((index, "success"))
        except DateConflictError as error:
            outcomes.put((index, error.dates))
        except BaseException as error:
            outcomes.put((index, type(error).__name__))

    workers = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(2)]
    with monkeypatch.context() as patch:
        patch.setattr(db, "connect", observe)
        try:
            for thread in workers:
                thread.start()
            start.wait()
            deadline = time.monotonic() + 15
            for thread in workers:
                thread.join(timeout=max(0, deadline - time.monotonic()))
        finally:
            start.abort()
            connected.abort()
            for thread in workers:
                thread.join(timeout=1)
        assert not any(thread.is_alive() for thread in workers)
    assert len(connections) == 2
    assert connections[0][0] != connections[1][0]
    assert connections[0][1] is not connections[1][1]
    results = sorted(outcomes.get_nowait() for _ in range(2))
    assert sum(value == "success" for _, value in results) == 1
    assert sum(value == ("2026-12-03",) for _, value in results) == 1
    after = state(migrated_db)
    user_won = results[0][1] == "success"
    assert len(after["reservations"]) == int(user_won)
    assert [r[0] for r in after["reserved_dates"]] == (["2026-12-02", "2026-12-03"] if user_won else ["2026-12-03", "2026-12-04"])
    assert len(after["reservation_logs"]) == 1


def test_tc006_12_stable_logs_and_projection(migrated_db):
    reserve(migrated_db)
    for day in ["2026-12-04", "2026-12-05", "2026-12-06"]:
        admin.block_dates(migrated_db, dates=[day], clock=clock)
    admin.update_settings(migrated_db, booking_horizon_days=30, clock=clock)
    seen = []
    for page in range(1, 4):
        result = admin.get_logs(migrated_db, page=page, page_size=2)
        assert result["total"] == 5
        assert result["page"] == page and result["page_size"] == 2
        for row in result["logs"]:
            assert set(row) == {"id", "reservation_id", "action", "detail", "created_at"}
            assert row["created_at"] == NOW.isoformat()
            assert set(row["detail"]) == {"before", "after"}
            seen.append(row["id"])
    assert seen == [5, 4, 3, 2, 1]
    assert admin.get_logs(migrated_db, page=4, page_size=2)["logs"] == []
    assert len(admin.get_logs(migrated_db, page_size=100)["logs"]) == 5
    assert admin.get_logs(migrated_db)["page_size"] == 50
    assert admin.get_logs(migrated_db, page=2_147_483_647)["logs"] == []


@pytest.mark.parametrize("params", [{"page": 0}, {"page": -1}, {"page": True}, {"page": "1"}, {"page": 2_147_483_648}, {"page_size": 0}, {"page_size": -1}, {"page_size": 101}, {"page_size": True}, {"page_size": "2"}])
def test_tc006_12_invalid_pagination(migrated_db, params):
    with pytest.raises(InvalidInputError):
        admin.get_logs(migrated_db, **params)


def test_tc006_13_horizon_preserves_existing_and_applies_to_additions(migrated_db):
    a = reserve(migrated_db, dates=["2027-02-01", "2027-02-02"])
    admin.block_dates(migrated_db, dates=["2027-02-03"], clock=clock)
    before = state(migrated_db)
    assert admin.update_settings(migrated_db, booking_horizon_days=7, clock=clock) == {"booking_horizon_days": 7}
    after = state(migrated_db)
    assert after["reservations"] == before["reservations"]
    assert after["reserved_dates"] == before["reserved_dates"]
    assert len(after["reservation_logs"]) == len(before["reservation_logs"]) + 1
    assert admin.get_settings(migrated_db) == {"booking_horizon_days": 7}
    admin.update_reservation(migrated_db, id=a["id"], changes={"name": "changed"}, clock=clock)
    admin.update_reservation(migrated_db, id=a["id"], changes={"dates": ["2026-12-07", "2027-02-01"]}, clock=clock)
    before_invalid = state(migrated_db)
    with pytest.raises(InvalidInputError):
        admin.update_reservation(migrated_db, id=a["id"], changes={"dates": ["2026-12-08", "2027-02-01"]}, clock=clock)
    assert state(migrated_db) == before_invalid
    admin.cancel_reservation(migrated_db, id=a["id"], clock=clock)
    admin.unblock_date(migrated_db, "2027-02-03", clock=clock)
    assert state(migrated_db)["reserved_dates"] == []


@pytest.mark.parametrize("value", [1, 366])
def test_tc006_13_settings_boundaries(migrated_db, value):
    assert admin.update_settings(migrated_db, booking_horizon_days=value, clock=clock)["booking_horizon_days"] == value
    after = state(migrated_db)
    admin.update_settings(migrated_db, booking_horizon_days=value, clock=clock)
    assert state(migrated_db) == after


@pytest.mark.parametrize("value", [0, -1, 367, True, False, "7", 7.0, None])
def test_tc006_13_invalid_setting_preserves_everything(migrated_db, value):
    before = state(migrated_db)
    with pytest.raises(InvalidInputError):
        admin.update_settings(migrated_db, booking_horizon_days=value, clock=clock)
    assert state(migrated_db) == before


def test_tc006_14_log_fields_cannot_leak_extra_token_data(migrated_db):
    a = reserve(migrated_db)
    with closing(db.connect(migrated_db)) as connection:
        connection.execute("UPDATE reservation_logs SET detail = ?", (json.dumps({"before": [], "after": a["dates"], "cancel_token": a["cancel_token"], "name": "PRIVATE_SENTINEL"}),))
    logs = admin.get_logs(migrated_db)
    assert a["cancel_token"] not in json.dumps(logs)
    assert "PRIVATE_SENTINEL" not in json.dumps(logs)


@pytest.mark.parametrize("operation", ["update", "cancel", "block", "unblock", "settings"])
def test_tc006_15_last_log_failure_rolls_back_each_operation(migrated_db, operation):
    a = reserve(migrated_db)
    admin.block_dates(migrated_db, dates=["2026-12-04"], clock=clock)
    with closing(db.connect(migrated_db)) as connection:
        connection.execute("CREATE TRIGGER reject_management_log BEFORE INSERT ON reservation_logs BEGIN SELECT RAISE(ABORT, 'synthetic log failure'); END")
    before = state(migrated_db)
    with pytest.raises(sqlite3.IntegrityError):
        _operation(migrated_db, operation, a["id"])
    assert state(migrated_db) == before


def _operation(path, operation, id, **options):
    options = {"clock": clock, **options}
    if operation == "update":
        return admin.update_reservation(path, id=id, changes={"name": "changed", "memo": "new", "dates": ["2026-12-03", "2026-12-05"]}, **options)
    if operation == "cancel":
        return admin.cancel_reservation(path, id=id, **options)
    if operation == "block":
        return admin.block_dates(path, dates=["2026-12-05", "2026-12-06"], **options)
    if operation == "unblock":
        return admin.unblock_date(path, "2026-12-04", **options)
    return admin.update_settings(path, booking_horizon_days=30, **options)


@pytest.mark.parametrize("operation", ["update", "cancel", "block", "unblock", "settings"])
def test_tc006_15_real_busy_and_rollback(migrated_db, operation):
    a = reserve(migrated_db)
    admin.block_dates(migrated_db, dates=["2026-12-04"], clock=clock)
    before = state(migrated_db)
    with db.write_transaction(migrated_db):
        with pytest.raises(TemporaryDatabaseError):
            _operation(migrated_db, operation, a["id"], busy_timeout_ms=0)
    assert state(migrated_db) == before


@pytest.mark.parametrize("changes", [{}, {"unknown": "x"}, {"name": None}, {"name": " "}, {"name": "a" * 41}, {"name": "\ud800"}, {"memo": None}, {"memo": "a" * 201}, {"dates": None}, {"dates": []}, {"dates": ["2026-12-02", "2026-12-02"]}, {"dates": ["2026-11-30"]}])
def test_tc006_05_invalid_patch_is_atomic(migrated_db, changes):
    a = reserve(migrated_db)
    before = state(migrated_db)
    with pytest.raises(InvalidInputError):
        admin.update_reservation(migrated_db, id=a["id"], changes=changes, clock=clock)
    assert state(migrated_db) == before


@pytest.mark.parametrize("params", [{"dates": []}, {"dates": ["2026-12-02"] * 2}, {"dates": ["2026-11-30"]}, {"dates": ["2027-03-01"]}, {"dates": ["2026-12-02"], "memo": "a" * 201}, {"dates": ["2026-12-02"], "memo": None}])
def test_tc006_09_invalid_block_is_atomic(migrated_db, params):
    before = state(migrated_db)
    with pytest.raises(InvalidInputError):
        admin.block_dates(migrated_db, **params, clock=clock)
    assert state(migrated_db) == before


@pytest.mark.parametrize("operation", ["update", "cancel"])
def test_tc006_07_missing_reservation(migrated_db, operation):
    before = state(migrated_db)
    with pytest.raises(admin.AdminNotFoundError):
        _operation(migrated_db, operation, "00000000-0000-4000-8000-000000000000")
    assert state(migrated_db) == before


@pytest.mark.parametrize("operation", ["update", "block"])
def test_tc006_15_date_pk_remains_final_conflict_guard(migrated_db, monkeypatch, operation):
    a = reserve(migrated_db)
    admin.block_dates(migrated_db, dates=["2026-12-05"], clock=clock)
    before = state(migrated_db)
    monkeypatch.setattr(admin, "find_conflicts", lambda *args: ())
    with pytest.raises(DateConflictError) as error:
        _operation(migrated_db, operation, a["id"])
    assert error.value.dates == ("2026-12-05",)
    assert state(migrated_db) == before


@pytest.mark.parametrize("operation", ["update", "block"])
def test_tc006_15_partial_occupancy_failure_rolls_back(migrated_db, operation):
    a = reserve(migrated_db)
    with closing(db.connect(migrated_db)) as connection:
        connection.execute("CREATE TRIGGER reject_second_occupancy BEFORE INSERT ON reserved_dates WHEN NEW.date = '2026-12-05' BEGIN SELECT RAISE(ABORT, 'synthetic occupancy failure'); END")
    before = state(migrated_db)
    with pytest.raises(sqlite3.IntegrityError):
        if operation == "block":
            admin.block_dates(migrated_db, dates=["2026-12-04", "2026-12-05"], clock=clock)
        else:
            _operation(migrated_db, operation, a["id"])
    assert state(migrated_db) == before


@pytest.mark.parametrize("value", ["0", "-1", "001", "broken", "", None])
def test_tc006_13_corrupt_settings_never_fall_back(migrated_db, value):
    with closing(db.connect(migrated_db)) as connection:
        if value is None:
            connection.execute("DELETE FROM settings WHERE key = 'booking_horizon_days'")
        else:
            connection.execute("UPDATE settings SET value = ? WHERE key = 'booking_horizon_days'", (value,))
    before = state(migrated_db)
    with pytest.raises(InvalidPolicyError):
        admin.get_settings(migrated_db)
    assert state(migrated_db) == before
