"""TC-006-01/02/03/14: fail-closed boundary, proxy evidence and CSRF."""

from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
import pytest

from app.config import SettingsError, load_settings
from app.db import connect, write_transaction
from app.main import create_app
from app.security import AdminContext, CSRF_TTL_SECONDS, resolve_admin_context
from app.services.reservations import create_reservation
from tests.db_helpers import snapshot


NOW = datetime(2026, 11, 30, 15, tzinfo=timezone.utc)
PROXY_SECRET = "QA_ONLY_NOT_A_REAL_PROXY_SECRET_01234567890123456789"
RID = "11111111-1111-4111-8111-111111111111"
ENDPOINTS = [
    ("GET", "/admin"), ("GET", "/admin/"), ("GET", "/admin/future"),
    ("GET", "/api/admin"), ("GET", "/api/admin/"),
    ("GET", "/api/admin/csrf"),
    ("GET", "/api/admin/reservations?month=2026-12"),
    ("POST", "/api/admin/reservations"),
    ("PATCH", f"/api/admin/reservations/{RID}"),
    ("DELETE", f"/api/admin/reservations/{RID}"),
    ("POST", "/api/admin/blocked-dates"),
    ("DELETE", "/api/admin/blocked-dates/2026-12-03"),
    ("GET", "/api/admin/logs"), ("GET", "/api/admin/settings"),
    ("PATCH", "/api/admin/settings"),
    ("PUT", "/api/admin/settings"), ("OPTIONS", "/api/admin/settings"),
    ("HEAD", "/api/admin/settings"), ("TRACE", "/admin/future"),
    ("GET", "/api/admin/reservations/"),
]
WRITES = [
    ("POST", "/api/admin/reservations", {"name": "QA_NEW", "dates": ["2026-12-04"]}, 201),
    ("PATCH", "/api/admin/reservations/{id}", {"memo": "QA_CHANGED"}, 200),
    ("DELETE", "/api/admin/reservations/{id}", None, 200),
    ("POST", "/api/admin/blocked-dates", {"dates": ["2026-12-05"], "memo": "QA_BLOCK"}, 201),
    ("DELETE", "/api/admin/blocked-dates/2026-12-03", None, 200),
    ("PATCH", "/api/admin/settings", {"booking_horizon_days": 30}, 200),
]


def db_snapshot(db_path):
    with closing(connect(db_path)) as connection:
        return snapshot(connection)


def trusted_app(settings):
    app = create_app(settings=settings, clock=lambda: NOW)
    app.dependency_overrides[resolve_admin_context] = lambda request: AdminContext()
    return app


@pytest.mark.parametrize("method,path", ENDPOINTS)
@pytest.mark.parametrize("host", ["public.test", "admin.test"])
def test_tc006_01_all_admin_routes_default_deny(settings, migrated_db, method, path, host):
    before = db_snapshot(migrated_db)
    app = create_app(settings=settings, clock=lambda: NOW)
    with TestClient(app, base_url=f"https://{host}") as client:
        response = client.request(method, path, headers={
            "X-RoomShare-Admin-Verified": "SUCCESS", "X-RoomShare-Proxy-Secret": PROXY_SECRET,
            "Forwarded": "for=127.0.0.1;host=admin.test;proto=https",
            "X-Forwarded-For": "127.0.0.1", "X-Forwarded-Host": "admin.test",
            "X-SSL-Client-Verify": "SUCCESS", "Origin": "https://admin.test",
        })
    assert response.status_code == 403
    if method != "HEAD":
        assert response.json()["code"] == "ADMIN_FORBIDDEN"
    assert response.headers["cache-control"] == "no-store"
    assert db_snapshot(migrated_db) == before


@pytest.mark.parametrize("host", ["public.test", "admin.test", "ADMIN.TEST", "admin.test:443", "admin.test."])
@pytest.mark.parametrize("context", [None, AdminContext(False), AdminContext(), True])
@pytest.mark.parametrize("method", ["GET", "PATCH"])
def test_tc006_02_host_and_injected_context_matrix(settings, migrated_db, host, context, method):
    app = trusted_app(settings)
    before = db_snapshot(migrated_db)
    with TestClient(app, base_url="https://admin.test") as client:
        token = client.get("/api/admin/csrf").json()["csrf_token"]
        app.dependency_overrides[resolve_admin_context] = lambda request: context
        response = client.request(method, "/api/admin/settings", headers={
            "Host": host, "Origin": "https://admin.test", "X-CSRF-Token": token,
        }, **({"json": {"booking_horizon_days": 30}} if method == "PATCH" else {}))
    allowed = host == "admin.test" and type(context) is AdminContext and context.verified
    assert response.status_code == (200 if allowed else 403)
    assert response.headers["cache-control"] == "no-store"
    if not allowed or method == "GET":
        assert db_snapshot(migrated_db) == before


@pytest.mark.parametrize("peer", ["127.0.0.1", "::1", "192.0.2.1", "testclient", "127.0.0.2"])
@pytest.mark.parametrize("signal", [None, "FAILED", "SUCCESS"])
@pytest.mark.parametrize("secret", [None, "wrong", PROXY_SECRET])
@pytest.mark.parametrize("method", ["GET", "PATCH"])
def test_tc006_02_production_proxy_requires_every_factor(settings, migrated_db, peer, signal, secret, method):
    configured = replace(settings, admin_access="proxy_mtls", admin_proxy_secret=PROXY_SECRET)
    app = create_app(settings=configured, clock=lambda: NOW)
    headers = {"Forwarded": "for=127.0.0.1;host=admin.test", "X-Forwarded-For": "127.0.0.1"}
    if signal is not None:
        headers["X-RoomShare-Admin-Verified"] = signal
    if secret is not None:
        headers["X-RoomShare-Proxy-Secret"] = secret
    if method == "PATCH":
        with TestClient(app, base_url="https://admin.test", client=("127.0.0.1", 12345)) as issuer:
            token = issuer.get("/api/admin/csrf", headers={
                "X-RoomShare-Admin-Verified": "SUCCESS", "X-RoomShare-Proxy-Secret": PROXY_SECRET,
            }).json()["csrf_token"]
        headers.update({"Origin": "https://admin.test", "X-CSRF-Token": token})
    before = db_snapshot(migrated_db)
    with TestClient(app, base_url="https://admin.test", client=(peer, 12345)) as client:
        response = client.request(method, "/api/admin/settings", headers=headers,
                                  **({"json": {"booking_horizon_days": 30}} if method == "PATCH" else {}))
    allowed = peer in ("127.0.0.1", "::1") and signal == "SUCCESS" and secret == PROXY_SECRET
    assert response.status_code == (200 if allowed else 403)
    if not allowed or method == "GET":
        assert db_snapshot(migrated_db) == before


def test_tc006_01_root_path_cannot_bypass_guard(settings, migrated_db):
    before = db_snapshot(migrated_db)
    with TestClient(create_app(settings=settings), base_url="https://admin.test", root_path="/prefix") as client:
        response = client.get("/prefix/api/admin/settings")
        assert response.status_code == 403
        assert response.headers["cache-control"] == "no-store"
    assert db_snapshot(migrated_db) == before


def test_tc006_14_root_path_preserves_private_responses_and_limits(settings, migrated_db):
    app = trusted_app(replace(settings, rate_limit_requests=1))
    with TestClient(app, base_url="https://admin.test", root_path="/prefix") as client:
        response = client.get("/prefix/api/admin/settings")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        response = client.get("/prefix/api/admin/settings")
        assert response.status_code == 429
        assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("duplicate", ["Host", "X-RoomShare-Admin-Verified", "X-RoomShare-Proxy-Secret"])
def test_tc006_02_duplicate_trust_headers_are_rejected(settings, migrated_db, duplicate):
    app = create_app(settings=replace(settings, admin_access="proxy_mtls", admin_proxy_secret=PROXY_SECRET))
    values = {"Host": "admin.test", "X-RoomShare-Admin-Verified": "SUCCESS", "X-RoomShare-Proxy-Secret": PROXY_SECRET}
    headers = list(values.items()) + [(duplicate, values[duplicate])]
    with TestClient(app, base_url="https://admin.test", client=("127.0.0.1", 12345)) as client:
        response = client.get("/api/admin/settings", headers=headers)
    assert response.status_code == 403


@pytest.mark.parametrize("value", [None, "", "short", "a" * 42, "a" * 129, "!" * 43, "한" * 43, 42])
def test_tc006_02_proxy_mode_requires_secret(settings, value):
    with pytest.raises(SettingsError, match="ADMIN_PROXY_SECRET") as error:
        replace(settings, admin_access="proxy_mtls", admin_proxy_secret=value)
    if isinstance(value, str) and len(value) > 3:
        assert value not in str(error.value)


def test_tc006_02_proxy_secret_environment_and_repr(settings):
    configured = load_settings(env_file=None, environ={
        "ROOMSHARE_DB_PATH": str(settings.db_path), "ROOMSHARE_ADMIN_ACCESS": "proxy_mtls",
        "ROOMSHARE_ADMIN_PROXY_SECRET": PROXY_SECRET,
    })
    assert configured.admin_proxy_secret == PROXY_SECRET
    assert PROXY_SECRET not in repr(configured)
    with pytest.raises(SettingsError):
        replace(settings, admin_access="development")


@pytest.mark.parametrize("method,path,body,success", WRITES)
@pytest.mark.parametrize("case", [
    "valid", "origin_missing", "origin_http", "origin_other", "origin_port", "origin_slash",
    "origin_duplicate", "token_missing", "token_wrong", "token_expired", "token_future",
    "token_duplicate", "token_other_app", "token_tampered",
])
def test_tc006_03_every_write_requires_exact_origin_and_valid_csrf(
    settings, migrated_db, method, path, body, success, case,
):
    reservation = create_reservation(migrated_db, name="QA_ORIGINAL", dates=["2026-12-02"], clock=lambda: NOW)
    with write_transaction(migrated_db) as connection:
        connection.execute("INSERT INTO reserved_dates(date,type,memo) VALUES (?, 'BLOCKED', ?)", ("2026-12-03", "QA_BLOCK"))
    before = db_snapshot(migrated_db)
    app = trusted_app(settings)
    with TestClient(app, base_url="https://admin.test") as client:
        token = client.get("/api/admin/csrf").json()["csrf_token"]
        headers = [("Origin", "https://admin.test"), ("X-CSRF-Token", token)]
        origin_cases = {"origin_http": "http://admin.test", "origin_other": "https://public.test",
                        "origin_port": "https://admin.test:443", "origin_slash": "https://admin.test/"}
        if case == "origin_missing":
            headers.pop(0)
        elif case in origin_cases:
            headers[0] = ("Origin", origin_cases[case])
        elif case == "origin_duplicate":
            headers.append(headers[0])
        elif case == "token_missing":
            headers.pop()
        elif case == "token_wrong":
            headers[1] = ("X-CSRF-Token", "invalid")
        elif case == "token_expired":
            app.state.clock = lambda: NOW + timedelta(seconds=CSRF_TTL_SECONDS)
        elif case == "token_future":
            app.state.clock = lambda: NOW - timedelta(seconds=1)
        elif case == "token_duplicate":
            headers.append(headers[1])
        elif case == "token_other_app":
            with TestClient(trusted_app(settings), base_url="https://admin.test") as other:
                headers[1] = ("X-CSRF-Token", other.get("/api/admin/csrf").json()["csrf_token"])
        elif case == "token_tampered":
            headers[1] = ("X-CSRF-Token", token[:-1] + ("0" if token[-1] != "0" else "1"))
        response = client.request(method, path.format(id=reservation["id"]), headers=headers,
                                  **({"json": body} if body is not None else {}))
    assert response.status_code == (success if case == "valid" else 403)
    assert response.headers["cache-control"] == "no-store"
    if case != "valid":
        assert response.json()["code"] == "CSRF_REJECTED"
        assert db_snapshot(migrated_db) == before


def test_tc006_03_csrf_is_fresh_private_and_valid_until_boundary(settings, migrated_db):
    app = trusted_app(settings)
    before = db_snapshot(migrated_db)
    with TestClient(app, base_url="https://admin.test") as client:
        first = client.get("/api/admin/csrf")
        second = client.get("/api/admin/csrf")
        assert first.json()["csrf_token"] != second.json()["csrf_token"]
        assert first.json()["expires_in"] == CSRF_TTL_SECONDS
        assert first.headers["cache-control"] == "no-store"
        assert "set-cookie" not in first.headers
        assert db_snapshot(migrated_db) == before
        app.state.clock = lambda: NOW + timedelta(seconds=CSRF_TTL_SECONDS - 1)
        response = client.patch("/api/admin/settings", json={"booking_horizon_days": 30}, headers={
            "Origin": "https://admin.test", "X-CSRF-Token": first.json()["csrf_token"],
        })
        assert response.status_code == 200


def test_tc006_14_guard_and_handler_failures_are_private(settings, migrated_db, caplog):
    app = trusted_app(settings)

    @app.get("/api/admin/test-failure")
    def failure():
        raise RuntimeError("QA_PRIVATE_FAILURE_SENTINEL")

    with TestClient(app, base_url="https://admin.test") as client:
        response = client.get("/api/admin/test-failure")
        assert response.status_code == 500
        assert response.json() == {"code": "INTERNAL_ERROR", "message": "요청을 처리할 수 없습니다"}
        assert response.headers["cache-control"] == "no-store"
        assert "QA_PRIVATE_FAILURE_SENTINEL" not in response.text + caplog.text

        def broken_resolver(request):
            raise RuntimeError("QA_PRIVATE_RESOLVER_SENTINEL")

        app.dependency_overrides[resolve_admin_context] = broken_resolver
        response = client.get("/api/admin/settings")
        assert response.status_code == 500
        assert response.headers["cache-control"] == "no-store"
        assert "QA_PRIVATE_RESOLVER_SENTINEL" not in response.text + caplog.text


def test_tc006_14_admin_body_and_request_limits(settings, migrated_db):
    app = trusted_app(replace(settings, max_body_bytes=32))
    before = db_snapshot(migrated_db)
    with TestClient(app, base_url="https://admin.test") as client:
        token = client.get("/api/admin/csrf").json()["csrf_token"]
        response = client.post("/api/admin/reservations", content=b"x" * 33, headers={
            "Origin": "https://admin.test", "X-CSRF-Token": token, "Content-Type": "application/json",
        })
        assert response.status_code == 413
        assert response.headers["cache-control"] == "no-store"
    app = trusted_app(replace(settings, rate_limit_requests=1))
    with TestClient(app, base_url="https://admin.test") as client:
        assert client.get("/api/admin/csrf").status_code == 200
        response = client.get("/api/admin/settings")
        assert response.status_code == 429
        assert response.headers["cache-control"] == "no-store"
        assert int(response.headers["retry-after"]) >= 1
    assert db_snapshot(migrated_db) == before
