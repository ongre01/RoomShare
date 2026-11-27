"""TC-004-12 request limits, plus TC-004-10 private error boundaries."""

import asyncio
from dataclasses import replace
import json
from threading import Barrier, Thread

import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.config import Settings, SettingsError, load_settings
from app.middleware import PublicAPIMiddleware


async def echo_size(scope, receive, send):
    request = Request(scope, receive)
    response = JSONResponse({"bytes": len(await request.body())}, headers={"Cache-Control": "public"})
    await response(scope, receive, send)


def middleware(app=echo_size, **changes):
    values = {"max_body_bytes": 16, "rate_limit_requests": 3, "rate_limit_window_seconds": 60}
    values.update(changes)
    return PublicAPIMiddleware(app, **values)


def exchange(app, chunks=(b"",), *, headers=(), path="/api/reservations", method="POST"):
    """Exercise ASGI transport directly, including inputs HTTP clients normalize."""
    async def run():
        messages = [
            {"type": "http.request", "body": body, "more_body": index < len(chunks) - 1}
            for index, body in enumerate(chunks)
        ]
        replies = []

        async def receive():
            if messages:
                return messages.pop(0)
            return {"type": "http.disconnect"}

        async def send(message):
            replies.append(message)

        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": method, "scheme": "http", "path": path,
            "raw_path": path.encode("ascii"), "query_string": b"", "root_path": "",
            "headers": list(headers), "client": ("127.0.0.1", 1234), "server": ("test", 80),
        }
        await app(scope, receive, send)
        return replies

    replies = asyncio.run(run())
    start = next(message for message in replies if message["type"] == "http.response.start")
    body = b"".join(message.get("body", b"") for message in replies if message["type"] == "http.response.body")
    return start["status"], dict(start["headers"]), json.loads(body)


@pytest.mark.parametrize("size", [15, 16, 17], ids=["below", "at", "over"])
@pytest.mark.parametrize("content_length", ["actual", "omitted", "forged"])
def test_tc004_12_actual_body_size_boundary(size, content_length):
    headers = []
    if content_length != "omitted":
        length = size if content_length == "actual" else 1
        headers.append((b"content-length", str(length).encode("ascii")))
    status, response_headers, payload = exchange(
        middleware(), (b"x" * (size - 1), b"x"), headers=headers,
    )
    assert status == (413 if size > 16 else 200)
    assert payload == (
        {"code": "REQUEST_TOO_LARGE", "message": "요청 본문이 너무 큽니다"}
        if size > 16 else {"bytes": size}
    )
    assert response_headers[b"cache-control"] == b"no-store"
    assert b"access-control-allow-origin" not in response_headers


@pytest.mark.parametrize("headers", [
    [(b"content-length", b"-1")], [(b"content-length", b"x")],
    [(b"content-length", b"")], [(b"content-length", b"1"), (b"content-length", b"1")],
])
def test_tc004_12_invalid_content_length_is_private_error(headers):
    status, response_headers, payload = exchange(middleware(), headers=headers)
    assert status == 400
    assert payload == {"code": "INVALID_REQUEST", "message": "올바른 요청이 아닙니다"}
    assert response_headers[b"cache-control"] == b"no-store"


def test_tc004_12_huge_content_length_does_not_parse_unbounded_integer():
    status, headers, payload = exchange(middleware(), headers=[(b"content-length", b"9" * 5000)])
    assert status == 413
    assert payload["code"] == "REQUEST_TOO_LARGE"
    assert headers[b"cache-control"] == b"no-store"


def test_tc004_12_fixed_window_boundary_and_forged_forwarding():
    now = [100.0]
    app = middleware(monotonic=lambda: now[0])
    for index in range(3):
        status, _, _ = exchange(app, headers=[(b"x-forwarded-for", f"192.0.2.{index}".encode())])
        assert status == 200
    now[0] = 159.99
    status, headers, payload = exchange(app, headers=[(b"forwarded", b"for=192.0.2.99")])
    assert status == 429
    assert payload == {"code": "RATE_LIMITED", "message": "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요"}
    assert headers[b"retry-after"] == b"1"
    assert headers[b"cache-control"] == b"no-store"
    now[0] = 160.0
    assert exchange(app)[0] == 200


def test_tc004_12_counter_is_shared_across_public_routes_and_methods():
    app = middleware(rate_limit_requests=2)
    assert exchange(app, path="/api/calendar", method="GET")[0] == 200
    assert exchange(app, path="/api/reservations/cancel", method="OPTIONS")[0] == 200
    assert exchange(app, path="/api/reservations/")[0] == 429
    assert exchange(app, path="/api/reservations/unknown")[0] == 429
    # Unrelated routes belong to their own future access/limiting policy.
    assert exchange(app, path="/healthz", method="GET")[0] == 200
    assert exchange(app, path="/api/admin/reservations", method="GET")[0] == 200


def test_tc004_12_apps_have_independent_counters():
    first = middleware(rate_limit_requests=1)
    second = middleware(rate_limit_requests=1)
    assert exchange(first)[0] == 200
    assert exchange(first)[0] == 429
    assert exchange(second)[0] == 200


def test_tc004_12_concurrent_clients_cannot_exceed_global_limit():
    app = middleware(rate_limit_requests=2)
    barrier = Barrier(5, timeout=5)
    results = []
    errors = []

    def worker():
        try:
            barrier.wait()
            results.append(exchange(app)[0])
        except BaseException as error:
            errors.append(error)

    workers = [Thread(target=worker, daemon=True) for _ in range(5)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)
    assert not any(worker.is_alive() for worker in workers)
    assert not errors
    assert sorted(results) == [200, 200, 429, 429, 429]


def test_tc004_10_unexpected_error_is_generic_no_store_without_log(caplog):
    async def failing(scope, receive, send):
        raise RuntimeError("QA_PRIVATE_EXCEPTION_SENTINEL")

    status, headers, payload = exchange(middleware(failing))
    assert status == 500
    assert payload == {"code": "INTERNAL_ERROR", "message": "요청을 처리할 수 없습니다"}
    assert headers[b"cache-control"] == b"no-store"
    assert "QA_PRIVATE_EXCEPTION_SENTINEL" not in caplog.text


def test_tc004_10_started_response_failure_is_sanitized_not_replaced():
    async def failing(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        raise RuntimeError("QA_PRIVATE_EXCEPTION_SENTINEL")

    with pytest.raises(RuntimeError, match="Public API response failed") as error:
        exchange(middleware(failing))
    assert "QA_PRIVATE_EXCEPTION_SENTINEL" not in str(error.value)
    assert error.value.__suppress_context__ is True


@pytest.mark.parametrize("field", ["max_body_bytes", "rate_limit_requests", "rate_limit_window_seconds"])
@pytest.mark.parametrize("value", [0, -1, True, False, 1.5, "0", "", "bad", None, 2147483648])
def test_tc004_12_invalid_limit_setting_fails_startup(field, value, db_path):
    with pytest.raises(SettingsError, match=field.upper()):
        Settings(db_path=db_path, **{field: value})
    assert not db_path.exists()


def test_tc004_12_limits_load_process_over_file_over_defaults(tmp_path):
    defaults = load_settings(env_file=None, environ={})
    assert (defaults.max_body_bytes, defaults.rate_limit_requests, defaults.rate_limit_window_seconds) == (16384, 120, 60)
    dotenv = tmp_path / ".env"
    dotenv.write_text("ROOMSHARE_MAX_BODY_BYTES=4096\nROOMSHARE_RATE_LIMIT_REQUESTS=30\nROOMSHARE_RATE_LIMIT_WINDOW_SECONDS=20\n")
    from_file = load_settings(env_file=dotenv, environ={})
    assert (from_file.max_body_bytes, from_file.rate_limit_requests, from_file.rate_limit_window_seconds) == (4096, 30, 20)
    from_process = load_settings(env_file=dotenv, environ={"ROOMSHARE_RATE_LIMIT_REQUESTS": " 12 "})
    assert from_process.rate_limit_requests == 12
    assert from_process.max_body_bytes == 4096
    assert from_process.rate_limit_window_seconds == 20
    with pytest.raises(SettingsError, match="RATE_LIMIT_REQUESTS"):
        load_settings(env_file=dotenv, environ={"ROOMSHARE_RATE_LIMIT_REQUESTS": ""})
    assert replace(from_file, rate_limit_requests=1).rate_limit_requests == 1


def test_tc004_12_real_api_body_rejection_preserves_database(settings, migrated_db):
    from contextlib import closing
    from datetime import datetime, timezone
    from fastapi.testclient import TestClient
    from app.db import connect
    from app.main import create_app
    from tests.db_helpers import snapshot

    payload = json.dumps({"name": "QA_USER_A", "dates": ["2026-12-02"]}).encode()
    app = create_app(
        replace(settings, max_body_bytes=len(payload)),
        clock=lambda: datetime(2026, 11, 30, 15, tzinfo=timezone.utc),
    )
    with TestClient(app) as client:
        success = client.post("/api/reservations", content=payload, headers={"Content-Type": "application/json"})
        assert success.status_code == 201
        with closing(connect(migrated_db)) as connection:
            before = snapshot(connection)
        failure = client.post(
            "/api/reservations", content=payload + b" ",
            headers={"Content-Type": "application/json", "Content-Length": "1"},
        )
        assert failure.status_code == 413
        assert failure.json()["code"] == "REQUEST_TOO_LARGE"
        assert failure.headers["Cache-Control"] == "no-store"
        with closing(connect(migrated_db)) as connection:
            assert snapshot(connection) == before


def test_tc004_12_real_api_throttling_preserves_database(settings, migrated_db):
    from contextlib import closing
    from datetime import datetime, timezone
    from fastapi.testclient import TestClient
    from app.db import connect
    from app.main import create_app
    from tests.db_helpers import snapshot

    app = create_app(
        replace(settings, rate_limit_requests=1),
        clock=lambda: datetime(2026, 11, 30, 15, tzinfo=timezone.utc),
    )
    with TestClient(app) as client:
        first = client.post("/api/reservations", json={"name": "QA_USER_A", "dates": ["2026-12-02"]})
        assert first.status_code == 201
        with closing(connect(migrated_db)) as connection:
            before = snapshot(connection)
        second = client.post(
            "/api/reservations", json={"name": "QA_USER_B", "dates": ["2026-12-03"]},
            headers={"X-Forwarded-For": "192.0.2.42", "Forwarded": "for=192.0.2.42"},
        )
        assert second.status_code == 429
        assert second.json()["code"] == "RATE_LIMITED"
        assert second.headers["Cache-Control"] == "no-store"
        assert 1 <= int(second.headers["Retry-After"]) <= 60
        with closing(connect(migrated_db)) as connection:
            assert snapshot(connection) == before
