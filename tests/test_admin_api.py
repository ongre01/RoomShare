"""RS-006 HTTP contracts using authenticated test context and real SQLite."""

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import logging

from fastapi.testclient import TestClient
import pytest

from app.db import connect, write_transaction
from app.main import create_app
from app.services.cancellation import cancel_reservation
from app.services.reservations import create_reservation
from tests.db_helpers import assert_db_invariants, snapshot


NOW = datetime(2026, 11, 30, 15, tzinfo=timezone.utc)
EARLIER = datetime(2026, 11, 29, 15, tzinfo=timezone.utc)
PAYLOAD = {"name": "QA_USER_A", "memo": "QA_MEMO_SENTINEL", "dates": ["2026-12-03", "2026-12-02"]}
ROOT = "/api/admin"


@pytest.fixture
def admin_app(settings, migrated_db):
    from app.security import AdminContext, resolve_admin_context

    app = create_app(settings, clock=lambda: NOW, busy_timeout_ms=25)
    app.dependency_overrides[resolve_admin_context] = lambda request: AdminContext()
    return app


@pytest.fixture
def admin_client(admin_app):
    with TestClient(admin_app, base_url="https://admin.test") as client:
        csrf = client.get(ROOT + "/csrf")
        assert csrf.status_code == 200
        client.headers.update({"Origin": "https://admin.test", "X-CSRF-Token": csrf.json()["csrf_token"]})
        yield client


def db_snapshot(db_path):
    with closing(connect(db_path)) as connection:
        assert_db_invariants(connection)
        return snapshot(connection)


def assert_error(response, status, code):
    assert response.status_code == status
    assert response.json()["code"] == code
    assert set(response.json()) == {"code", "message"}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-type"] == "application/json"


def create(client, **changes):
    response = client.post(ROOT + "/reservations", json={**PAYLOAD, **changes})
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def test_tc006_04_creation_and_month_queries_return_full_dates(admin_client, migrated_db):
    dates = ["2027-01-02", "2026-12-31"]
    booking = create(admin_client, dates=dates)
    assert set(booking) == {"id", "dates", "status"}
    assert booking["dates"] == sorted(dates) and booking["status"] == "CONFIRMED"
    before = db_snapshot(migrated_db)
    for month in ["2026-12", "2027-01"]:
        response = admin_client.get(ROOT + "/reservations", params={"month": month})
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        records = response.json()["reservations"]
        assert len(records) == 1
        row = records[0]
        assert set(row) == {"id", "dates", "name", "memo", "status", "created_at", "cancelled_at"}
        assert row["id"] == booking["id"] and row["dates"] == sorted(dates)
        assert row["name"] == PAYLOAD["name"] and row["memo"] == PAYLOAD["memo"]
        assert row["created_at"] == NOW.isoformat() and row["cancelled_at"] is None
    assert admin_client.get(ROOT + "/reservations?month=2027-02").json() == {"reservations": []}
    assert db_snapshot(migrated_db) == before


def test_tc006_04_creation_text_boundaries_and_omitted_memo(admin_client, migrated_db):
    response = admin_client.post(ROOT + "/reservations", json={"name": "  " + "가" * 40 + "  ", "dates": ["2026-12-02"]})
    assert response.status_code == 201
    with closing(connect(migrated_db)) as connection:
        assert tuple(connection.execute("SELECT name, memo FROM reservations").fetchone()) == ("가" * 40, "")
    create(admin_client, name="나", memo="메" * 200, dates=["2026-12-03"])


def test_tc006_05_self_dates_and_partial_fields_are_preserved(admin_client, migrated_db):
    booking = create(admin_client)
    path = ROOT + "/reservations/" + booking["id"]
    response = admin_client.patch(path, json={"name": "QA_USER_B", "dates": ["2026-12-04", "2026-12-02"]})
    assert response.status_code == 200
    assert response.json()["dates"] == ["2026-12-02", "2026-12-04"]
    assert response.json()["name"] == "QA_USER_B" and response.json()["memo"] == PAYLOAD["memo"]
    with closing(connect(migrated_db)) as connection:
        assert [row[0] for row in connection.execute("SELECT date FROM reserved_dates ORDER BY date")] == ["2026-12-02", "2026-12-04"]
        logs = connection.execute("SELECT detail FROM reservation_logs ORDER BY id").fetchall()
        assert len(logs) == 2
        assert json.loads(logs[-1][0])["before"] == ["2026-12-02", "2026-12-03"]
        assert json.loads(logs[-1][0])["after"] == ["2026-12-02", "2026-12-04"]
    changed = admin_client.patch(path, json={"memo": "QA_NEW_MEMO"})
    assert changed.status_code == 200
    assert changed.json()["dates"] == response.json()["dates"]
    assert changed.json()["name"] == "QA_USER_B" and changed.json()["memo"] == "QA_NEW_MEMO"
    db_snapshot(migrated_db)


@pytest.mark.parametrize("occupied", ["RESERVED", "BLOCKED"])
def test_tc006_06_edit_conflict_preserves_every_table(admin_client, migrated_db, occupied):
    booking = create(admin_client)
    if occupied == "RESERVED":
        create(admin_client, dates=["2026-12-05"])
    else:
        assert admin_client.post(ROOT + "/blocked-dates", json={"dates": ["2026-12-05"]}).status_code == 201
    before = db_snapshot(migrated_db)
    response = admin_client.patch(ROOT + "/reservations/" + booking["id"], json={"name": "QA_CHANGED", "memo": "QA_CHANGED_MEMO", "dates": ["2026-12-02", "2026-12-05"]})
    assert response.status_code == 409
    assert response.json() == {"code": "DATE_CONFLICT", "message": "예약할 수 없는 날짜입니다", "dates": ["2026-12-05"]}
    assert response.headers["cache-control"] == "no-store"
    assert db_snapshot(migrated_db) == before


def test_tc006_07_cancellation_repeat_and_status_filter(admin_client, migrated_db):
    dates = ["2026-12-31", "2027-01-02"]
    booking = create(admin_client, dates=dates)
    path = ROOT + "/reservations/" + booking["id"]
    cancelled = admin_client.delete(path)
    assert cancelled.status_code == 200
    assert cancelled.json() == {"id": booking["id"], "status": "CANCELLED"}
    create(admin_client, dates=dates)
    before = db_snapshot(migrated_db)
    repeated = admin_client.delete(path)
    assert repeated.status_code == 200 and repeated.json() == cancelled.json()
    assert db_snapshot(migrated_db) == before
    for month in ["2026-12", "2027-01"]:
        rows = admin_client.get(ROOT + "/reservations", params={"month": month, "status": "CANCELLED"}).json()["reservations"]
        assert len(rows) == 1 and rows[0]["dates"] == dates and rows[0]["cancelled_at"] == NOW.isoformat()
        assert rows[0]["id"] == booking["id"]
        all_rows = admin_client.get(ROOT + "/reservations", params={"month": month, "status": "ALL"}).json()["reservations"]
        assert len(all_rows) == 2
        confirmed = admin_client.get(ROOT + "/reservations", params={"month": month}).json()["reservations"]
        assert len(confirmed) == 1 and confirmed[0]["id"] != booking["id"]
    assert_error(admin_client.patch(path, json={"memo": "must not change"}), 409, "CHANGE_NOT_ALLOWED")
    assert db_snapshot(migrated_db) == before


def test_tc006_07_public_cancellation_history_is_listed(admin_client, migrated_db):
    booking = create_reservation(migrated_db, **PAYLOAD, clock=lambda: NOW)
    cancel_reservation(migrated_db, id=booking["id"], cancel_token=booking["cancel_token"], clock=lambda: NOW)
    rows = admin_client.get(ROOT + "/reservations?month=2026-12&status=CANCELLED").json()["reservations"]
    assert len(rows) == 1 and rows[0]["id"] == booking["id"]
    assert rows[0]["dates"] == sorted(PAYLOAD["dates"])


@pytest.mark.parametrize("method,body", [("PATCH", {"dates": ["2026-12-04"]}), ("PATCH", {"memo": "QA_CHANGED"}), ("PATCH", {"name": "QA_CHANGED"}), ("DELETE", None)])
def test_tc006_08_past_booking_changes_are_denied(admin_client, migrated_db, method, body):
    booking = create_reservation(migrated_db, **{**PAYLOAD, "dates": ["2026-11-30", "2026-12-03"]}, clock=lambda: EARLIER)
    before = db_snapshot(migrated_db)
    response = admin_client.request(method, ROOT + "/reservations/" + booking["id"], **({"json": body} if body is not None else {}))
    assert_error(response, 409, "CHANGE_NOT_ALLOWED")
    assert db_snapshot(migrated_db) == before


def test_tc006_08_today_is_allowed_for_admin(admin_client, migrated_db):
    booking = create(admin_client, dates=["2026-12-01"])
    path = ROOT + "/reservations/" + booking["id"]
    assert admin_client.patch(path, json={"memo": "today"}).status_code == 200
    assert admin_client.delete(path).status_code == 200
    db_snapshot(migrated_db)


@pytest.mark.parametrize("occupied", ["RESERVED", "BLOCKED"])
def test_tc006_09_partial_block_conflict_is_atomic(admin_client, migrated_db, occupied):
    if occupied == "RESERVED":
        create(admin_client, dates=["2026-12-03"])
    else:
        assert admin_client.post(ROOT + "/blocked-dates", json={"dates": ["2026-12-03"]}).status_code == 201
    before = db_snapshot(migrated_db)
    response = admin_client.post(ROOT + "/blocked-dates", json={"dates": ["2026-12-02", "2026-12-03", "2026-12-04"], "memo": "QA_BLOCK"})
    assert response.status_code == 409 and response.json()["dates"] == ["2026-12-03"]
    assert db_snapshot(migrated_db) == before


def test_tc006_10_unblock_cannot_remove_reserved_or_missing_dates(admin_client, migrated_db):
    create(admin_client, dates=["2026-12-02"])
    blocked = admin_client.post(ROOT + "/blocked-dates", json={"dates": ["2026-12-03"], "memo": "QA_BLOCK"})
    assert blocked.status_code == 201
    assert blocked.json() == {"dates": ["2026-12-03"], "status": "BLOCKED"}
    before = db_snapshot(migrated_db)
    for day in ["2026-12-02", "2026-12-04"]:
        assert_error(admin_client.delete(ROOT + "/blocked-dates/" + day), 404, "NOT_FOUND")
        assert db_snapshot(migrated_db) == before
    response = admin_client.delete(ROOT + "/blocked-dates/2026-12-03")
    assert response.status_code == 200 and response.json() == {"date": "2026-12-03", "status": "AVAILABLE"}
    after = db_snapshot(migrated_db)
    assert after["reservations"] == before["reservations"]
    assert len(after["reserved_dates"]) == 1 and after["reserved_dates"][0][0] == "2026-12-02"
    assert len(after["reservation_logs"]) == len(before["reservation_logs"]) + 1


def test_tc006_12_pagination_is_stable_for_identical_timestamps(admin_client, migrated_db):
    for day in range(2, 7):
        create(admin_client, dates=[f"2026-12-{day:02d}"])
    before = db_snapshot(migrated_db)
    pages = [admin_client.get(ROOT + "/logs", params={"page": page, "page_size": 2}) for page in range(1, 5)]
    assert all(response.status_code == 200 for response in pages)
    assert [len(response.json()["logs"]) for response in pages] == [2, 2, 1, 0]
    ids = [row["id"] for response in pages for row in response.json()["logs"]]
    assert ids == [5, 4, 3, 2, 1]
    assert [response.json()["page"] for response in pages] == [1, 2, 3, 4]
    assert all(response.json()["total"] == 5 and response.json()["page_size"] == 2 for response in pages)
    assert all(row["created_at"] == NOW.isoformat() for response in pages for row in response.json()["logs"])
    assert admin_client.get(ROOT + "/logs").json()["page_size"] == 50
    assert len(admin_client.get(ROOT + "/logs?page_size=100").json()["logs"]) == 5
    assert db_snapshot(migrated_db) == before


@pytest.mark.parametrize("query", ["page=0", "page=-1", "page=1.5", "page=true", "page_size=0", "page_size=-1", "page_size=101", "page_size=1.5", "page_size=true", "page=1&page=2", "page_size=1&page_size=2", "extra=private"])
def test_tc006_12_bad_pagination_is_rejected(admin_client, migrated_db, query):
    before = db_snapshot(migrated_db)
    assert_error(admin_client.get(ROOT + "/logs?" + query), 422, "INVALID_INPUT")
    assert db_snapshot(migrated_db) == before


def test_tc006_13_horizon_change_preserves_old_occupancy(admin_client, migrated_db):
    booking = create(admin_client, dates=["2027-02-27"])
    assert admin_client.post(ROOT + "/blocked-dates", json={"dates": ["2027-02-28"]}).status_code == 201
    before = db_snapshot(migrated_db)
    assert admin_client.get(ROOT + "/settings").json() == {"booking_horizon_days": 90}
    response = admin_client.patch(ROOT + "/settings", json={"booking_horizon_days": 1})
    assert response.status_code == 200 and response.json() == {"booking_horizon_days": 1}
    after = db_snapshot(migrated_db)
    assert after["reserved_dates"] == before["reserved_dates"] and after["reservations"] == before["reservations"]
    assert len(after["reservation_logs"]) == len(before["reservation_logs"]) + 1
    assert admin_client.get(ROOT + "/settings").json() == response.json()
    edit = admin_client.patch(ROOT + "/reservations/" + booking["id"], json={"memo": "still editable", "dates": ["2027-02-27"]})
    assert edit.status_code == 200 and edit.json()["dates"] == ["2027-02-27"]
    assert_error(admin_client.post(ROOT + "/reservations", json=PAYLOAD), 422, "INVALID_INPUT")
    maximum = admin_client.patch(ROOT + "/settings", json={"booking_horizon_days": 366})
    assert maximum.status_code == 200 and maximum.json() == {"booking_horizon_days": 366}


@pytest.mark.parametrize("value", [0, -1, 367, 1000000, True, False, 1.5, "90", None])
def test_tc006_13_invalid_horizon_has_no_changes(admin_client, migrated_db, value):
    before = db_snapshot(migrated_db)
    assert_error(admin_client.patch(ROOT + "/settings", json={"booking_horizon_days": value}), 422, "INVALID_INPUT")
    assert db_snapshot(migrated_db) == before


def test_tc006_14_private_data_only_in_authorized_reservation_fields(admin_client, migrated_db, caplog):
    booking = create_reservation(migrated_db, **PAYLOAD, clock=lambda: NOW)
    token_hash = hashlib.sha256(booking["cancel_token"].encode()).hexdigest()
    with caplog.at_level(logging.DEBUG):
        listed = admin_client.get(ROOT + "/reservations?month=2026-12")
        logs = admin_client.get(ROOT + "/logs")
        public = admin_client.get("https://public.test/api/calendar?month=2026-12")
        invalid = admin_client.patch(ROOT + "/reservations/" + booking["id"], json={"name": {"private": "QA_PRIVATE_SENTINEL"}})
    assert listed.status_code == logs.status_code == public.status_code == 200
    assert_error(invalid, 422, "INVALID_INPUT")
    assert listed.json()["reservations"][0]["name"] == PAYLOAD["name"]
    observed = caplog.text + listed.text + logs.text + public.text + invalid.text
    for forbidden in [booking["cancel_token"], token_hash, "cancel_token", "QA_PRIVATE_SENTINEL"]:
        assert forbidden not in observed
    with closing(connect(migrated_db)) as connection:
        stored_logs = repr([tuple(row) for row in connection.execute("SELECT * FROM reservation_logs")])
    for forbidden in [PAYLOAD["name"], PAYLOAD["memo"], booking["cancel_token"], token_hash]:
        assert forbidden not in caplog.text + logs.text + public.text + invalid.text + stored_logs
    for response in [listed, logs, public, invalid]:
        assert response.headers["cache-control"] == "no-store"


def mutation_request(operation, booking_id):
    return {
        "create": ("POST", ROOT + "/reservations", {**PAYLOAD, "dates": ["2026-12-06"]}),
        "edit": ("PATCH", ROOT + "/reservations/" + booking_id, {"name": "QA_CHANGED", "dates": ["2026-12-02", "2026-12-04"]}),
        "cancel": ("DELETE", ROOT + "/reservations/" + booking_id, None),
        "block": ("POST", ROOT + "/blocked-dates", {"dates": ["2026-12-06", "2026-12-07"]}),
        "unblock": ("DELETE", ROOT + "/blocked-dates/2026-12-05", None),
        "settings": ("PATCH", ROOT + "/settings", {"booking_horizon_days": 7}),
    }[operation]


@pytest.mark.parametrize("operation", ["create", "edit", "cancel", "block", "unblock", "settings"])
def test_tc006_15_log_failure_is_private_and_rolls_back(admin_client, migrated_db, caplog, operation):
    booking = create(admin_client)
    assert admin_client.post(ROOT + "/blocked-dates", json={"dates": ["2026-12-05"]}).status_code == 201
    with closing(connect(migrated_db)) as connection:
        connection.execute("CREATE TRIGGER admin_log_failure BEFORE INSERT ON reservation_logs "
                           "BEGIN SELECT RAISE(ABORT, 'QA_SQL_SECRET'); END")
    before = db_snapshot(migrated_db)
    method, path, body = mutation_request(operation, booking["id"])
    with caplog.at_level(logging.DEBUG):
        response = admin_client.request(method, path, **({"json": body} if body is not None else {}))
    assert_error(response, 500, "INTERNAL_ERROR")
    assert "QA_SQL_SECRET" not in response.text + caplog.text
    assert db_snapshot(migrated_db) == before


@pytest.mark.parametrize("operation", ["create", "edit", "cancel", "block", "unblock", "settings"])
def test_tc006_15_busy_writer_returns_503_without_change(admin_client, migrated_db, operation):
    booking = create(admin_client)
    assert admin_client.post(ROOT + "/blocked-dates", json={"dates": ["2026-12-05"]}).status_code == 201
    before = db_snapshot(migrated_db)
    method, path, body = mutation_request(operation, booking["id"])
    with write_transaction(migrated_db):
        response = admin_client.request(method, path, **({"json": body} if body is not None else {}))
    assert_error(response, 503, "DATABASE_BUSY")
    assert db_snapshot(migrated_db) == before


@pytest.mark.parametrize("changes", [{"name": None}, {"name": True}, {"name": " "}, {"name": "가" * 41}, {"name": "\ud800"}, {"memo": None}, {"memo": 1}, {"memo": "메" * 201}, {"memo": "\udfff"}, {"dates": []}, {"dates": None}, {"dates": "2026-12-02"}, {"dates": [1]}, {"dates": ["2026-12-02", "2026-12-02"]}, {"dates": ["2026-12-2"]}, {"dates": ["2027-02-29"]}, {"unknown": "QA_PRIVATE"}])
@pytest.mark.parametrize("method", ["POST", "PATCH"])
def test_tc006_04_05_invalid_reservation_payloads(admin_client, migrated_db, method, changes):
    booking = create(admin_client) if method == "PATCH" else None
    path = ROOT + "/reservations" + ("/" + booking["id"] if booking else "")
    body = changes if method == "PATCH" else {**PAYLOAD, **changes}
    before = db_snapshot(migrated_db)
    response = admin_client.request(method, path, content=json.dumps(body), headers={"content-type": "application/json"})
    assert_error(response, 422, "INVALID_INPUT")
    assert db_snapshot(migrated_db) == before


@pytest.mark.parametrize("method,path", [("POST", "/reservations"), ("PATCH", "/reservations/missing"), ("POST", "/blocked-dates"), ("PATCH", "/settings")])
@pytest.mark.parametrize("body,status,code", [(b'{"private":"QA_PRIVATE",', 400, "INVALID_JSON"), (b'\xff', 400, "INVALID_JSON"), (b'null', 422, "INVALID_INPUT"), (b'[]', 422, "INVALID_INPUT"), (b'{}', 422, "INVALID_INPUT")])
def test_tc006_14_malformed_bodies_are_private(admin_client, migrated_db, method, path, body, status, code):
    before = db_snapshot(migrated_db)
    response = admin_client.request(method, ROOT + path, content=body, headers={"content-type": "application/json"})
    assert_error(response, status, code)
    assert "QA_PRIVATE" not in response.text and "traceback" not in response.text.lower()
    assert db_snapshot(migrated_db) == before


@pytest.mark.parametrize("path", ["/reservations", "/reservations?month=2026-13", "/reservations?month=2026-12&month=2027-01", "/reservations?month=2026-12&status=UNKNOWN", "/reservations?month=2026-12&status=ALL&status=CONFIRMED", "/reservations?month=2026-12&extra=private", "/settings?extra=private", "/csrf?extra=private"])
def test_tc006_14_unknown_or_ambiguous_query_is_rejected(admin_client, migrated_db, path):
    before = db_snapshot(migrated_db)
    assert_error(admin_client.get(ROOT + path), 422, "INVALID_INPUT")
    assert db_snapshot(migrated_db) == before


@pytest.mark.parametrize("operation", ["create", "edit", "cancel", "block", "unblock", "settings"])
def test_tc006_14_mutation_query_is_rejected(admin_client, migrated_db, operation):
    booking = create(admin_client)
    before = db_snapshot(migrated_db)
    method, path, body = mutation_request(operation, booking["id"])
    response = admin_client.request(method, path + "?unsupported=value", **({"json": body} if body is not None else {}))
    assert_error(response, 422, "INVALID_INPUT")
    assert db_snapshot(migrated_db) == before


@pytest.mark.parametrize("method,path", [("POST", "/reservations"), ("PATCH", "/reservations/missing"), ("POST", "/blocked-dates"), ("PATCH", "/settings")])
def test_tc006_14_non_json_media_is_rejected(admin_client, method, path):
    assert_error(admin_client.request(method, ROOT + path, content="{}", headers={"content-type": "text/plain"}), 415, "UNSUPPORTED_MEDIA_TYPE")


@pytest.mark.parametrize("patch", [{"memo": None}, {"memo": "메" * 201}, {"memo": "\ud800"}, {"dates": []}, {"dates": ["2026-12-02", "2026-12-02"]}, {"dates": ["2026-11-30"]}, {"dates": ["2027-03-01"]}, {"unknown": "QA_PRIVATE"}])
def test_tc006_09_block_payload_validation_preserves_database(admin_client, migrated_db, patch):
    before = db_snapshot(migrated_db)
    response = admin_client.post(ROOT + "/blocked-dates", content=json.dumps({"dates": ["2026-12-02"], **patch}), headers={"content-type": "application/json"})
    assert_error(response, 422, "INVALID_INPUT")
    assert db_snapshot(migrated_db) == before


@pytest.mark.parametrize("method,path,body,status,code", [
    ("DELETE", "/reservations/missing", None, 404, "NOT_FOUND"),
    ("PATCH", "/reservations/missing", {"memo": "QA_PRIVATE"}, 404, "NOT_FOUND"),
    ("DELETE", "/blocked-dates/2026-12-2", None, 422, "INVALID_INPUT"),
    ("GET", "/unknown", None, 404, "NOT_FOUND"),
    ("PUT", "/settings", {}, 405, "METHOD_NOT_ALLOWED"),
])
def test_tc006_14_missing_routes_and_targets_are_private(admin_client, migrated_db, method, path, body, status, code):
    before = db_snapshot(migrated_db)
    response = admin_client.request(method, ROOT + path, **({"json": body} if body is not None else {}))
    assert_error(response, status, code)
    assert db_snapshot(migrated_db) == before


@pytest.mark.parametrize("method,path", [("GET", "/settings"), ("DELETE", "/reservations/missing"), ("DELETE", "/blocked-dates/2026-12-02")])
def test_tc006_14_get_and_delete_reject_bodies(admin_client, migrated_db, method, path):
    before = db_snapshot(migrated_db)
    response = admin_client.request(method, ROOT + path, json={"private": "QA_PRIVATE"})
    assert_error(response, 422, "INVALID_INPUT")
    assert db_snapshot(migrated_db) == before
