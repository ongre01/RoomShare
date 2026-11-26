"""RS-004 public HTTP contracts against a migrated, isolated SQLite file."""

from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import logging
from threading import Barrier, Thread
from queue import Queue

from fastapi.testclient import TestClient
import pytest

from app.db import connect, write_transaction
from app.main import create_app
from app.services.reservations import create_reservation
from tests.db_helpers import assert_db_invariants, snapshot


NOW = datetime(2026, 11, 30, 15, tzinfo=timezone.utc)
PAYLOAD = {"name": "QA_USER_A", "memo": "QA_MEMO_SENTINEL", "dates": ["2026-12-04", "2026-12-02"]}


@pytest.fixture
def public_app(settings, migrated_db):
    return create_app(settings, clock=lambda: NOW, busy_timeout_ms=25)


@pytest.fixture
def public_client(public_app):
    with TestClient(public_app, base_url="http://public.test") as client:
        yield client


def db_snapshot(db_path):
    with closing(connect(db_path)) as connection:
        assert_db_invariants(connection)
        return snapshot(connection)


def credentials(result):
    return {key: result[key] for key in ("id", "cancel_token")}


def assert_error(response, status, code):
    assert response.status_code == status
    assert response.json()["code"] == code
    assert set(response.json()) == {"code", "message"}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-type"] == "application/json"


def test_tc004_01_calendar_contract(public_client, migrated_db):
    result = public_client.post("/api/reservations", json=PAYLOAD).json()
    with write_transaction(migrated_db) as connection:
        connection.execute("INSERT INTO reserved_dates (date, type, memo) VALUES (?, ?, ?)",
                           ("2026-12-03", "BLOCKED", "QA_BLOCK_PRIVATE"))
    response = public_client.get("/api/calendar", params={"month": "2026-12"})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"dates", "today", "min_date", "max_date"}
    assert (body["today"], body["min_date"], body["max_date"]) == ("2026-12-01", "2026-12-01", "2027-02-28")
    assert len(body["dates"]) == 31
    assert all(set(day) == {"date", "status"} for day in body["dates"])
    assert [day["status"] for day in body["dates"][:4]] == ["AVAILABLE", "RESERVED", "BLOCKED", "RESERVED"]
    for value in [PAYLOAD["name"], PAYLOAD["memo"], result["id"], result["cancel_token"], "QA_BLOCK_PRIVATE"]:
        assert value not in response.text
    past = public_client.get("/api/calendar?month=2026-11").json()
    assert all(day["status"] == "PAST" for day in past["dates"])
    outside = public_client.get("/api/calendar?month=2027-03").json()
    assert outside["dates"][0] == {"date": "2027-03-01", "status": "AVAILABLE"}


def test_tc004_02_creation_contract_and_database(public_client, migrated_db):
    response = public_client.post("/api/reservations", json=PAYLOAD)
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    result = response.json()
    assert set(result) == {"id", "dates", "status", "cancel_token"}
    assert result["dates"] == sorted(PAYLOAD["dates"])
    assert result["status"] == "CONFIRMED"
    with closing(connect(migrated_db)) as connection:
        row = connection.execute("SELECT * FROM reservations").fetchone()
        assert row["id"] == result["id"]
        assert row["name"] == PAYLOAD["name"] and row["memo"] == PAYLOAD["memo"]
        assert row["cancel_token_hash"] == hashlib.sha256(result["cancel_token"].encode()).hexdigest()
        assert connection.execute("SELECT COUNT(*) FROM reserved_dates").fetchone()[0] == 2
        logs = connection.execute("SELECT action, detail FROM reservation_logs").fetchall()
        assert len(logs) == 1 and logs[0]["action"] == "CREATE"
        assert json.loads(logs[0]["detail"]) == {"before": [], "after": sorted(PAYLOAD["dates"])}
        assert_db_invariants(connection)


@pytest.mark.parametrize("occupied", ["RESERVED", "BLOCKED"])
def test_tc004_03_conflict_preserves_all_rows(public_client, migrated_db, occupied):
    if occupied == "RESERVED":
        assert public_client.post("/api/reservations", json={**PAYLOAD, "dates": ["2026-12-03"]}).status_code == 201
    else:
        with write_transaction(migrated_db) as connection:
            connection.execute("INSERT INTO reserved_dates (date, type) VALUES (?, ?)", ("2026-12-03", "BLOCKED"))
    before = db_snapshot(migrated_db)
    response = public_client.post("/api/reservations", json={**PAYLOAD, "dates": ["2026-12-02", "2026-12-03", "2026-12-04"]})
    assert response.status_code == 409
    assert response.json() == {"code": "DATE_CONFLICT", "message": "예약할 수 없는 날짜입니다", "dates": ["2026-12-03"]}
    assert response.headers["cache-control"] == "no-store"
    assert db_snapshot(migrated_db) == before


@pytest.mark.parametrize("patch", [
    {"unknown": "QA_PRIVATE_EXTRA"}, {"name": 123}, {"name": True}, {"name": None},
    {"name": ""}, {"name": " \t"}, {"name": "이" * 41},
    {"name": "\ud800"}, {"memo": "\udfff"},
    {"memo": None}, {"memo": 123}, {"memo": "메" * 201},
    {"dates": "2026-12-02"}, {"dates": None}, {"dates": {}}, {"dates": []},
    {"dates": [12]}, {"dates": [True]}, {"dates": [None]},
    {"dates": ["2026-12-02", "2026-12-02"]}, {"dates": ["2026-12-2"]},
    {"dates": ["2026-11-30"]}, {"dates": ["2027-03-01"]}, {"dates": ["2027-02-29"]},
])
def test_tc004_04_invalid_creation_payload(public_client, migrated_db, patch):
    before = db_snapshot(migrated_db)
    response = public_client.post("/api/reservations", content=json.dumps({**PAYLOAD, **patch}),
                                  headers={"content-type": "application/json"})
    assert_error(response, 422, "INVALID_INPUT")
    assert response.json()["message"] == "입력값을 확인해 주세요"
    assert db_snapshot(migrated_db) == before


@pytest.mark.parametrize("path", ["/api/reservations", "/api/reservations/cancel"])
@pytest.mark.parametrize("raw,status,code", [
    (b'{"name":"QA_PRIVATE_INVALID_JSON",', 400, "INVALID_JSON"),
    (b'\xff', 400, "INVALID_JSON"),
    (b'{}', 422, "INVALID_INPUT"), (b'[]', 422, "INVALID_INPUT"),
    (b'null', 422, "INVALID_INPUT"), (b'42', 422, "INVALID_INPUT"),
    (b'[' * 1100 + b']' * 1099, 400, "INVALID_JSON"),
    (b'[' * 1100 + b']' * 1100, 422, "INVALID_INPUT"),
])
def test_tc004_04_malformed_body(public_client, migrated_db, path, raw, status, code):
    before = db_snapshot(migrated_db)
    response = public_client.post(path, content=raw, headers={"content-type": "application/json"})
    assert_error(response, status, code)
    assert "QA_PRIVATE" not in response.text and "traceback" not in response.text.lower()
    assert db_snapshot(migrated_db) == before


@pytest.mark.parametrize("query", ["", "?month=2026-13", "?month=2026-1", "?month=2026-12&extra=private", "?month=2026-12&month=2026-11"])
def test_tc004_04_invalid_calendar_query(public_client, query):
    assert_error(public_client.get("/api/calendar" + query), 422, "INVALID_INPUT")


@pytest.mark.parametrize("path,payload", [
    ("/api/reservations?extra=value", PAYLOAD),
    ("/api/reservations/cancel?cancel_token=unsupported", {"id": "missing", "cancel_token": "wrong"}),
])
def test_tc004_04_mutations_reject_query(public_client, migrated_db, path, payload):
    before = db_snapshot(migrated_db)
    assert_error(public_client.post(path, json=payload), 422, "INVALID_INPUT")
    assert db_snapshot(migrated_db) == before


@pytest.mark.parametrize("content_type", ["text/plain", "application/x-www-form-urlencoded", "application/problem+json", ""])
def test_tc004_04_only_json_media(public_client, content_type):
    response = public_client.post("/api/reservations", content=json.dumps(PAYLOAD), headers={"content-type": content_type})
    assert_error(response, 415, "UNSUPPORTED_MEDIA_TYPE")


def test_tc004_04_input_boundaries_and_omitted_memo(public_client, migrated_db):
    response = public_client.post("/api/reservations", json={"name": "  " + "가" * 40 + "  ", "dates": ["2026-12-02"]})
    assert response.status_code == 201
    with closing(connect(migrated_db)) as connection:
        row = connection.execute("SELECT name, memo FROM reservations").fetchone()
        assert tuple(row) == ("가" * 40, "")
    assert public_client.post("/api/reservations", json={"name": "나", "memo": "메" * 200, "dates": ["2026-12-03"]}).status_code == 201


def test_tc004_05_cancel_and_rebook(public_client, migrated_db):
    booking = public_client.post("/api/reservations", json=PAYLOAD).json()
    response = public_client.post("/api/reservations/cancel", json=credentials(booking))
    assert response.status_code == 200
    assert response.json() == {"id": booking["id"], "status": "CANCELLED"}
    assert response.headers["cache-control"] == "no-store"
    with closing(connect(migrated_db)) as connection:
        row = connection.execute("SELECT status, cancelled_at FROM reservations").fetchone()
        assert tuple(row) == ("CANCELLED", NOW.isoformat())
        assert connection.execute("SELECT COUNT(*) FROM reserved_dates").fetchone()[0] == 0
        logs = connection.execute("SELECT detail FROM reservation_logs WHERE action='CANCEL'").fetchall()
        assert len(logs) == 1
        assert json.loads(logs[0][0]) == {"before": sorted(PAYLOAD["dates"]), "after": []}
        assert_db_invariants(connection)
    assert public_client.post("/api/reservations", json=PAYLOAD).status_code == 201


def test_tc004_06_invalid_id_and_token_identical(public_client, migrated_db):
    booking = public_client.post("/api/reservations", json=PAYLOAD).json()
    before = db_snapshot(migrated_db)
    responses = []
    for body in [
        {"id": booking["id"], "cancel_token": "X" * 43},
        {"id": "99999999-9999-4999-8999-999999999999", "cancel_token": booking["cancel_token"]},
        {"id": "not-a-uuid", "cancel_token": booking["cancel_token"]},
        {"id": "", "cancel_token": ""},
        {"id": booking["id"], "cancel_token": "잘못된 토큰"},
    ]:
        response = public_client.post("/api/reservations/cancel", json=body)
        assert_error(response, 404, "CANCELLATION_NOT_FOUND")
        responses.append(response.json())
        assert db_snapshot(migrated_db) == before
    assert all(body == responses[0] for body in responses)


@pytest.mark.parametrize("body", [{}, {"id": 1, "cancel_token": "wrong"}, {"id": "id", "cancel_token": None}, {"id": "id", "cancel_token": "wrong", "extra": "secret"}])
def test_tc004_06_invalid_credential_structure(public_client, body):
    assert_error(public_client.post("/api/reservations/cancel", json=body), 422, "INVALID_INPUT")


@pytest.mark.parametrize("first_day", ["2026-12-01", "2026-11-30"])
def test_tc004_07_today_or_past_preserved(public_client, migrated_db, first_day):
    booking = create_reservation(migrated_db, name="QA_USER", dates=[first_day, "2026-12-04"],
                                 clock=lambda: datetime(2026, 11, 29, 15, tzinfo=timezone.utc))
    before = db_snapshot(migrated_db)
    response = public_client.post("/api/reservations/cancel", json=credentials(booking))
    assert_error(response, 409, "CANCELLATION_NOT_ALLOWED")
    assert db_snapshot(migrated_db) == before


def test_tc004_08_cancel_sql_failure_is_private_and_atomic(public_client, migrated_db, caplog):
    booking = public_client.post("/api/reservations", json=PAYLOAD).json()
    with closing(connect(migrated_db)) as connection:
        connection.execute("CREATE TRIGGER cancel_failure BEFORE INSERT ON reservation_logs "
                           "WHEN NEW.action='CANCEL' BEGIN SELECT RAISE(ABORT, 'QA_SQL_SECRET'); END")
    before = db_snapshot(migrated_db)
    with caplog.at_level(logging.DEBUG):
        response = public_client.post("/api/reservations/cancel", json=credentials(booking))
    assert_error(response, 500, "INTERNAL_ERROR")
    assert "QA_SQL_SECRET" not in response.text + caplog.text
    assert booking["cancel_token"] not in response.text + caplog.text
    assert db_snapshot(migrated_db) == before


def test_tc004_09_repeat_does_not_cancel_rebooking(public_client, migrated_db):
    booking = public_client.post("/api/reservations", json=PAYLOAD).json()
    first = public_client.post("/api/reservations/cancel", json=credentials(booking))
    assert first.status_code == 200
    assert public_client.post("/api/reservations", json=PAYLOAD).status_code == 201
    before = db_snapshot(migrated_db)
    repeated = public_client.post("/api/reservations/cancel", json=credentials(booking))
    assert repeated.status_code == 200 and repeated.json() == first.json()
    assert db_snapshot(migrated_db) == before
    assert_error(public_client.post("/api/reservations/cancel", json={"id": booking["id"], "cancel_token": "wrong"}), 404, "CANCELLATION_NOT_FOUND")


def test_tc004_09_concurrent_http_cancellation(settings, migrated_db):
    booking = create_reservation(migrated_db, **PAYLOAD, clock=lambda: NOW)
    app = create_app(settings, clock=lambda: NOW, busy_timeout_ms=5000)
    barrier, outcomes = Barrier(2), Queue()
    def worker():
        try:
            with TestClient(app) as client:
                barrier.wait(timeout=5)
                response = client.post("/api/reservations/cancel", json=credentials(booking))
                outcomes.put((response.status_code, response.json()))
        except BaseException as error:
            outcomes.put(error)
    threads = [Thread(target=worker, daemon=True) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()
    expected = (200, {"id": booking["id"], "status": "CANCELLED"})
    assert [outcomes.get_nowait() for _ in range(2)] == [expected, expected]
    with closing(connect(migrated_db)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM reservation_logs WHERE action='CANCEL'").fetchone()[0] == 1
        assert_db_invariants(connection)


def test_tc004_10_sensitive_responses_and_logs(public_client, migrated_db, caplog):
    with caplog.at_level(logging.DEBUG):
        created = public_client.post("/api/reservations", json=PAYLOAD)
        booking = created.json()
        responses = [
            public_client.get("/api/calendar?month=2026-12"),
            public_client.post("/api/reservations", json=PAYLOAD),
            public_client.post("/api/reservations", json={**PAYLOAD, "name": {"private": "QA_PRIVATE_SENTINEL"}}),
            public_client.post("/api/reservations/cancel", json={"id": booking["id"], "cancel_token": "QA_BAD_SECRET"}),
            public_client.post("/api/reservations/cancel", json=credentials(booking)),
            public_client.post("/api/reservations/cancel", json=credentials(booking)),
        ]
    assert [response.status_code for response in responses] == [200, 409, 422, 404, 200, 200]
    with closing(connect(migrated_db)) as connection:
        logs = repr([tuple(row) for row in connection.execute("SELECT * FROM reservation_logs")])
        dump = '\n'.join(connection.iterdump())
    forbidden = [booking["cancel_token"], hashlib.sha256(booking["cancel_token"].encode()).hexdigest(),
                 PAYLOAD["name"], PAYLOAD["memo"], "QA_PRIVATE_SENTINEL", "QA_BAD_SECRET"]
    observed = caplog.text + logs + ''.join(response.text for response in responses)
    for value in forbidden:
        assert value not in observed
    assert booking["cancel_token"] not in dump
    for response in [created, *responses]:
        assert response.headers["cache-control"] == "no-store"
        assert booking["cancel_token"] not in str(response.request.url)
        assert "location" not in response.headers


@pytest.mark.parametrize("path", ["/api/reservations", "/api/reservations/cancel"])
def test_tc004_11_cors_not_enabled(public_client, path):
    preflight = public_client.options(path, headers={"origin": "https://untrusted.test", "access-control-request-method": "POST", "access-control-request-headers": "content-type"})
    assert_error(preflight, 405, "METHOD_NOT_ALLOWED")
    actual = public_client.post(path, headers={"origin": "https://untrusted.test"}, json=PAYLOAD if path.endswith("reservations") else {"id": "missing", "cancel_token": "wrong"})
    assert actual.status_code == (201 if path.endswith("reservations") else 404)
    for response in [preflight, actual]:
        assert not any(key.lower().startswith("access-control-allow-") for key in response.headers)


def test_tc004_12_limits_are_wired_to_application(settings, migrated_db):
    app = create_app(replace(settings, max_body_bytes=200, rate_limit_requests=2), clock=lambda: NOW)
    before = db_snapshot(migrated_db)
    with TestClient(app) as client:
        assert_error(client.post("/api/reservations", content=b'x' * 201), 413, "REQUEST_TOO_LARGE")
        assert client.get("/api/calendar?month=2026-12").status_code == 200
        response = client.post("/api/reservations", json=PAYLOAD, headers={"x-forwarded-for": "198.51.100.23"})
        assert_error(response, 429, "RATE_LIMITED")
        assert int(response.headers["retry-after"]) > 0
    assert db_snapshot(migrated_db) == before


@pytest.mark.parametrize("cancel", [False, True])
def test_tc004_13_busy_maps_to_503(public_client, migrated_db, cancel):
    booking = public_client.post("/api/reservations", json=PAYLOAD).json() if cancel else None
    before = db_snapshot(migrated_db)
    with write_transaction(migrated_db):
        response = public_client.post("/api/reservations/cancel" if cancel else "/api/reservations", json=credentials(booking) if cancel else PAYLOAD)
    assert_error(response, 503, "DATABASE_BUSY")
    assert db_snapshot(migrated_db) == before


def test_tc004_14_sql_input_is_data(public_client, migrated_db):
    name, memo = "'; DROP TABLE reservations; --", "'); DELETE FROM reserved_dates; --"
    created = public_client.post("/api/reservations", json={**PAYLOAD, "name": name, "memo": memo})
    assert created.status_code == 201
    calendar = public_client.get("/api/calendar?month=2026-12")
    assert calendar.status_code == 200
    assert name not in calendar.text and memo not in calendar.text
    with closing(connect(migrated_db)) as connection:
        assert tuple(connection.execute("SELECT name, memo FROM reservations").fetchone()) == (name, memo)
        assert connection.execute("SELECT COUNT(*) FROM reserved_dates").fetchone()[0] == 2
        assert_db_invariants(connection)
