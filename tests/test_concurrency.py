"""TC-003-08/09: simultaneous real-file SQLite writers, repeated with fresh DBs."""

from contextlib import closing
from datetime import datetime, timezone
import json
from queue import Queue
import threading
import time

import pytest

from app import db
from app.services.errors import DateConflictError
from app.services.reservations import create_reservation
from tests.db_helpers import assert_db_invariants, snapshot


FIXED_NOW = datetime(2026, 11, 30, 15, tzinfo=timezone.utc)


def _assert_atomic_race(db_path, monkeypatch, requested_dates):
    real_connect = db.connect
    with closing(real_connect(db_path)) as connection:
        before = snapshot(connection)
    assert before["reservations"] == []
    assert before["reserved_dates"] == []
    assert before["reservation_logs"] == []

    # The first barrier starts both service calls together. The second ensures
    # both real connections are alive before either tries BEGIN IMMEDIATE.
    start = threading.Barrier(3, timeout=5)
    connected = threading.Barrier(2, timeout=5)
    connections = []
    records_lock = threading.Lock()
    outcomes = Queue()

    def observed_connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        try:
            with records_lock:
                # Keep the objects alive to prevent an id from being reused.
                connections.append((threading.get_ident(), connection))
            connected.wait()
        except BaseException:
            connection.close()
            raise
        return connection

    def worker(index):
        try:
            start.wait()
            result = create_reservation(
                db_path,
                name=f"QA_USER_{index}",
                memo="QA_MEMO_SENTINEL",
                dates=list(requested_dates[index]),
                clock=lambda: FIXED_NOW,
                busy_timeout_ms=5000,
            )
            # Do not retain or print the cancellation token in race diagnostics.
            outcomes.put((index, "success", {key: result[key] for key in ("id", "dates", "status")}))
        except DateConflictError as error:
            outcomes.put((index, "conflict", error.dates))
        except BaseException as error:
            outcomes.put((index, "unexpected", type(error).__name__))

    workers = [
        threading.Thread(target=worker, args=(index,), daemon=True, name=f"reservation-{index}")
        for index in range(2)
    ]
    with monkeypatch.context() as patch:
        patch.setattr(db, "connect", observed_connect)
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
        assert not any(thread.is_alive() for thread in workers), "SQLite writer did not terminate"

    assert len(connections) == 2
    assert connections[0][0] != connections[1][0], "Workers must use distinct threads"
    assert connections[0][1] is not connections[1][1], "Writers shared a SQLite connection"
    assert outcomes.qsize() == 2
    results = [outcomes.get_nowait() for _ in range(2)]
    unexpected = [(index, detail) for index, status, detail in results if status == "unexpected"]
    assert unexpected == []
    successes = [(index, detail) for index, status, detail in results if status == "success"]
    conflicts = [(index, detail) for index, status, detail in results if status == "conflict"]
    assert len(successes) == 1
    assert len(conflicts) == 1
    winner, result = successes[0]
    loser, conflict_dates = conflicts[0]
    winning_dates = sorted(requested_dates[winner])
    assert result["dates"] == winning_dates
    assert result["status"] == "CONFIRMED"
    assert conflict_dates == tuple(sorted(set(requested_dates[0]) & set(requested_dates[1])))

    # Reconnect only after both workers returned: check the committed database,
    # including the losing request's exclusive dates and unrelated settings.
    with closing(real_connect(db_path)) as connection:
        reservations = connection.execute("SELECT id, name, status FROM reservations").fetchall()
        assert [tuple(row) for row in reservations] == [
            (result["id"], f"QA_USER_{winner}", "CONFIRMED"),
        ]
        occupancy = connection.execute(
            "SELECT date, reservation_id, type FROM reserved_dates ORDER BY date"
        ).fetchall()
        assert [tuple(row) for row in occupancy] == [
            (value, result["id"], "RESERVED") for value in winning_dates
        ]
        occupied_dates = {row["date"] for row in occupancy}
        loser_only = set(requested_dates[loser]) - set(winning_dates)
        assert occupied_dates.isdisjoint(loser_only)
        logs = connection.execute("SELECT reservation_id, action, detail FROM reservation_logs").fetchall()
        assert len(logs) == 1
        assert logs[0]["reservation_id"] == result["id"]
        assert logs[0]["action"] == "CREATE"
        assert json.loads(logs[0]["detail"]) == {"before": [], "after": winning_dates}
        after = snapshot(connection)
        assert after["settings"] == before["settings"]
        assert after["schema_migrations"] == before["schema_migrations"]
        assert_db_invariants(connection)


@pytest.mark.parametrize("attempt", range(20), ids=lambda value: f"TC-003-08-run-{value + 1:02}")
def test_tc003_08_same_date_has_exactly_one_winner(migrated_db, monkeypatch, attempt):
    _assert_atomic_race(migrated_db, monkeypatch, (("2026-12-02",), ("2026-12-02",)))


@pytest.mark.parametrize("attempt", range(20), ids=lambda value: f"TC-003-09-run-{value + 1:02}")
def test_tc003_09_partial_overlap_has_no_losing_rows(migrated_db, monkeypatch, attempt):
    _assert_atomic_race(
        migrated_db, monkeypatch,
        (("2026-12-02", "2026-12-03"), ("2026-12-03", "2026-12-04")),
    )
