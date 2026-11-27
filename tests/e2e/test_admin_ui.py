"""TC-007-02..12: real browser, loopback server and isolated SQLite.

The browser keeps the real https://admin.test origin. A test-only transport
adapter forwards every request to loopback HTTP without changing Origin/CSRF.
Only authentication is injected in the server's Python test harness; application
Host/Origin/CSRF checks run unchanged. This is not a TLS/mTLS deployment test.
"""

from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit

import httpx
from playwright.sync_api import expect
import pytest

from app.db import connect, write_transaction
from app.migrations import migrate
from app.services.reservations import create_reservation
from app.services.admin import block_dates
from tests.db_helpers import assert_db_invariants, snapshot


ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 30, 15, tzinfo=timezone.utc)
SERVER_CODE = """
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import uvicorn
from app.config import Settings
from app.main import create_app
from app.security import AdminContext, resolve_admin_context
clock_file, access_file = Path(sys.argv[3]), Path(sys.argv[4])
def clock():
    return datetime(2026, 11, 30, 15, tzinfo=timezone.utc) + timedelta(seconds=int(clock_file.read_text()))
settings = Settings(db_path=sys.argv[1], public_host='public.test',
                    admin_host='admin.test', rate_limit_requests=10000)
app = create_app(settings=settings, clock=clock)
app.dependency_overrides[resolve_admin_context] = lambda request: (
    AdminContext() if access_file.read_text() == 'allow' else None
)
uvicorn.run(app, fd=int(sys.argv[2]), access_log=False, proxy_headers=False, log_level='warning')
"""


@dataclass
class AdminServer:
    url: str
    db_path: Path
    clock_file: Path
    access_file: Path
    sensitive_values: list[str] = field(default_factory=list, repr=False)


@pytest.fixture
def admin_server(tmp_path):
    db_path = tmp_path / "admin-browser.db"
    migrate(db_path)
    clock_file, access_file = tmp_path / "clock", tmp_path / "access"
    clock_file.write_text("0")
    access_file.write_text("allow")
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    url = f"http://127.0.0.1:{listener.getsockname()[1]}"
    process = subprocess.Popen(
        [sys.executable, "-c", SERVER_CODE, str(db_path), str(listener.fileno()),
         str(clock_file), str(access_file)],
        cwd=ROOT, pass_fds=(listener.fileno(),),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    listener.close()
    server = AdminServer(url, db_path, clock_file, access_file)
    try:
        deadline = time.monotonic() + 15
        with httpx.Client(base_url=url, timeout=0.5, trust_env=False) as client:
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise AssertionError("Admin test server exited before readiness")
                try:
                    if client.get("/healthz").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.05)
            else:
                raise AssertionError("Admin test server readiness timed out")
        yield server
        with closing(connect(db_path)) as connection:
            assert_db_invariants(connection)
    finally:
        process.terminate()
        try:
            output, _ = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)
            raise AssertionError("Admin test server failed to stop") from None
        assert process.returncode in (0, -15), "Admin test server failed"
        assert not any(value in output for value in server.sensitive_values), (
            "Sensitive synthetic input appeared in test server logs"
        )


@pytest.fixture
def admin_proxy(page, admin_server):
    # No production route, proxy secret or environment authentication bypass.
    with httpx.Client(base_url=admin_server.url, timeout=10, trust_env=False) as client:
        def send(request):
            url = urlsplit(request.url)
            headers = dict(request.all_headers())
            headers["host"] = url.netloc
            headers["accept-encoding"] = "identity"
            for key in ("content-length", "connection"):
                headers.pop(key, None)
            response = client.request(
                request.method, url.path + ("?" + url.query if url.query else ""),
                headers=headers, content=request.post_data_buffer,
            )
            if url.path == "/api/admin/csrf" and response.status_code == 200:
                admin_server.sensitive_values.append(response.json()["csrf_token"])
            return response

        def forward(route):
            response = send(route.request)
            route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)

        page.route("https://admin.test/**", forward)
        yield send
        page.unroute("https://admin.test/**", forward)


def db_snapshot(server):
    with closing(connect(server.db_path)) as connection:
        return snapshot(connection)


def create(server, dates, name="QA_USER_A", memo="QA_MEMO_SENTINEL"):
    result = create_reservation(server.db_path, name=name, dates=dates, memo=memo, clock=lambda: NOW)
    server.sensitive_values.extend([result["cancel_token"], name, memo])
    return result


def open_admin(page, admin_proxy):
    response = page.goto("https://admin.test/admin")
    assert response.status == 200
    assert response.headers["cache-control"] == "no-store"
    expect(day(page, "2026-12-02")).to_be_visible()
    ready(page)


def ready(page):
    expect(page.locator("#refresh-data")).to_be_enabled()


def day(page, date):
    return page.locator(f"#calendar-grid button[data-date='{date}']")


def card(page, rid):
    return page.locator(f"#reservation-list article[data-id='{rid}']")


def fill(page, dates="2026-12-02\n2026-12-03", name="QA_USER_A", memo="QA_MEMO_SENTINEL"):
    page.locator("#name").fill(name)
    page.locator("#memo").fill(memo)
    page.locator("#dates").fill(dates)


def submit(page, method="POST", suffix="/api/admin/reservations", selector="#save-reservation", status=201):
    with page.expect_response(lambda response: response.request.method == method and response.url.endswith(suffix)) as result:
        page.locator(selector).click()
    response = result.value
    assert response.status == status
    assert response.headers["cache-control"] == "no-store"
    ready(page)
    return response.json()


def writes(page):
    requests = []
    page.on("request", lambda request: requests.append(request) if request.method in {"POST", "PATCH", "DELETE"} else None)
    return requests


def test_tc009_02_manage_public_booking_and_review_committed_history(page, admin_server, admin_proxy):
    with httpx.Client(base_url=admin_server.url, trust_env=False) as public:
        created = public.post("/api/reservations", json={"name": "QA_USER_A", "dates": ["2026-12-02"]})
        assert created.status_code == 201
        rid = created.json()["id"]
        admin_server.sensitive_values.append(created.json()["cancel_token"])
    open_admin(page, admin_proxy)
    reservation = card(page, rid)
    expect(reservation).to_contain_text("QA_USER_A")
    reservation.get_by_role("button", name="수정", exact=True).click()
    fill(page, dates="2026-12-04", name="QA_EDITED")
    updated = submit(page, "PATCH", f"/api/admin/reservations/{rid}", status=200)
    assert updated["dates"] == ["2026-12-04"]
    expect(reservation).to_contain_text("QA_EDITED")
    page.locator("#block-dates").fill("2026-12-05")
    submit(page, "POST", "/api/admin/blocked-dates", "#save-block")
    expect(day(page, "2026-12-05")).to_contain_text("차단")
    before = db_snapshot(admin_server)
    page.locator("#block-dates").fill("2026-12-04\n2026-12-06")
    conflict = submit(page, "POST", "/api/admin/blocked-dates", "#save-block", 409)
    assert conflict["dates"] == ["2026-12-04"] and bool(db_snapshot(admin_server) == before)
    page.once("dialog", lambda dialog: dialog.accept())
    submit(page, "DELETE", "/api/admin/blocked-dates/2026-12-05", "#blocked-list [data-date='2026-12-05']", 200)
    expect(day(page, "2026-12-05")).to_contain_text("가능")
    page.locator("#booking-horizon").fill("14")
    submit(page, "PATCH", "/api/admin/settings", "#save-settings", 200)
    expect(page.locator("#current-horizon")).to_contain_text("14")
    page.once("dialog", lambda dialog: dialog.accept())
    with page.expect_response(lambda response: response.request.method == "DELETE") as cancelled:
        reservation.get_by_role("button", name="예약 취소", exact=True).click()
    assert cancelled.value.status == 200
    ready(page)
    expect(day(page, "2026-12-04")).to_contain_text("가능")
    actions = ["CREATE", "ADMIN_UPDATE", "BLOCK", "UNBLOCK", "SETTINGS", "ADMIN_CANCEL"]
    rows = page.locator("#logs li[data-id]")
    expect(rows).to_have_count(len(actions))
    for index, action in enumerate(reversed(actions)):
        expect(rows.nth(index)).to_contain_text(action)
    with closing(connect(admin_server.db_path)) as db:
        assert tuple(db.execute("SELECT name,status FROM reservations WHERE id=?", (rid,)).fetchone()) == (
            "QA_EDITED", "CANCELLED",
        )
        assert db.execute("SELECT count(*) FROM reserved_dates").fetchone()[0] == 0
        assert db.execute("SELECT value FROM settings WHERE key='booking_horizon_days'").fetchone()[0] == "14"
        assert [row[0] for row in db.execute("SELECT action FROM reservation_logs ORDER BY id")] == actions


def test_tc007_02_03_create_edit_refresh_and_csrf(page, admin_server, admin_proxy):
    open_admin(page, admin_proxy)
    sent = writes(page)
    fill(page)
    result = submit(page)
    assert len(sent) == 1
    headers = sent[0].all_headers()
    assert headers["origin"] == "https://admin.test"
    assert bool(headers.get("x-csrf-token"))
    assert "cancel_token" not in result
    expect(card(page, result["id"])).to_contain_text("QA_USER_A")
    expect(day(page, "2026-12-02")).to_contain_text("예약")
    expect(page.locator("#logs")).to_contain_text("CREATE")
    card(page, result["id"]).get_by_role("button", name="수정", exact=True).click()
    expect(page.locator("#edit-original")).to_contain_text("2026-12-03")
    fill(page, dates="2026-12-02\n2026-12-04", name="QA_EDITED", memo="QA_EDITED_MEMO")
    expect(page.locator("#edit-diff")).to_contain_text("2026-12-04")
    submit(page, "PATCH", f"/api/admin/reservations/{result['id']}", status=200)
    expect(card(page, result["id"])).to_contain_text("QA_EDITED")
    expect(day(page, "2026-12-03")).to_contain_text("가능")
    expect(day(page, "2026-12-04")).to_contain_text("예약")
    expect(page.locator("#logs")).to_contain_text("ADMIN_UPDATE")
    with closing(connect(admin_server.db_path)) as connection:
        row = connection.execute("SELECT name,memo FROM reservations WHERE id=?", (result["id"],)).fetchone()
        dates = [r[0] for r in connection.execute("SELECT date FROM reserved_dates ORDER BY date")]
    assert tuple(row) == ("QA_EDITED", "QA_EDITED_MEMO")
    assert dates == ["2026-12-02", "2026-12-04"]


def test_tc007_04_full_dates_survive_month_boundary_edit(page, admin_server, admin_proxy):
    result = create(admin_server, ["2026-12-31", "2027-01-02"])
    open_admin(page, admin_proxy)
    card(page, result["id"]).get_by_role("button", name="수정", exact=True).click()
    assert page.locator("#dates").input_value().split() == ["2026-12-31", "2027-01-02"]
    expect(page.locator("#edit-original")).to_contain_text("2027-01-02")
    page.locator("#name").fill("QA_RENAMED")
    updated = submit(page, "PATCH", f"/api/admin/reservations/{result['id']}", status=200)
    assert updated["dates"] == ["2026-12-31", "2027-01-02"]
    page.locator("#next-month").click()
    ready(page)
    expect(card(page, result["id"])).to_contain_text("QA_RENAMED")
    expect(day(page, "2027-01-02")).to_contain_text("예약")


def test_tc007_05_cancel_confirmation_and_one_request(page, admin_server, admin_proxy):
    result = create(admin_server, ["2026-12-02"])
    open_admin(page, admin_proxy)
    sent = writes(page)
    before = db_snapshot(admin_server)
    page.once("dialog", lambda dialog: dialog.dismiss())
    card(page, result["id"]).get_by_role("button", name="예약 취소", exact=True).click()
    assert sent == []
    assert db_snapshot(admin_server) == before
    page.once("dialog", lambda dialog: dialog.accept())
    with page.expect_response(lambda response: response.request.method == "DELETE") as response:
        card(page, result["id"]).get_by_role("button", name="예약 취소", exact=True).click()
    assert response.value.status == 200
    ready(page)
    assert len(sent) == 1
    expect(day(page, "2026-12-02")).to_contain_text("가능")
    with closing(connect(admin_server.db_path)) as connection:
        assert connection.execute("SELECT status FROM reservations WHERE id=?", (result["id"],)).fetchone()[0] == "CANCELLED"
        assert connection.execute("SELECT count(*) FROM reserved_dates").fetchone()[0] == 0
    expect(page.locator("#logs")).to_contain_text("ADMIN_CANCEL")


def test_tc007_06_block_conflict_and_confirmed_unblock(page, admin_server, admin_proxy):
    result = create(admin_server, ["2026-12-02"])
    open_admin(page, admin_proxy)
    page.locator("#block-dates").fill("2026-12-03")
    page.locator("#block-memo").fill("QA_BLOCK_MEMO")
    submit(page, "POST", "/api/admin/blocked-dates", "#save-block")
    expect(day(page, "2026-12-03")).to_contain_text("차단")
    before = db_snapshot(admin_server)
    page.locator("#block-dates").fill("2026-12-02\n2026-12-04")
    submit(page, "POST", "/api/admin/blocked-dates", "#save-block", 409)
    assert db_snapshot(admin_server) == before
    expect(page.locator("#block-dates")).to_have_value("2026-12-02\n2026-12-04")
    expect(card(page, result["id"])).to_be_visible()
    sent = writes(page)
    button = page.locator("#blocked-list button[data-date='2026-12-03']")
    page.once("dialog", lambda dialog: dialog.dismiss())
    button.click()
    assert sent == []
    assert db_snapshot(admin_server) == before
    page.once("dialog", lambda dialog: dialog.accept())
    with page.expect_response(lambda response: response.request.method == "DELETE") as response:
        button.click()
    assert response.value.status == 200
    ready(page)
    assert len(sent) == 1
    expect(day(page, "2026-12-03")).to_contain_text("가능")
    expect(card(page, result["id"])).to_be_visible()
    expect(page.locator("#logs")).to_contain_text("UNBLOCK")


def test_tc007_07_actual_conflict_preserves_original_and_draft(page, admin_server, admin_proxy):
    result = create(admin_server, ["2026-12-02", "2026-12-03"])
    open_admin(page, admin_proxy)
    card(page, result["id"]).get_by_role("button", name="수정", exact=True).click()
    fill(page, dates="2026-12-02\n2026-12-05", name="QA_DRAFT", memo="QA_DRAFT_MEMO")
    create(admin_server, ["2026-12-05"], name="QA_COMPETITOR")
    before = db_snapshot(admin_server)
    submit(page, "PATCH", f"/api/admin/reservations/{result['id']}", status=409)
    expect(page.locator("#status")).to_contain_text("409")
    assert db_snapshot(admin_server) == before
    expect(page.locator("#name")).to_have_value("QA_DRAFT")
    expect(page.locator("#memo")).to_have_value("QA_DRAFT_MEMO")
    expect(page.locator("#dates")).to_have_value("2026-12-02\n2026-12-05")
    expect(page.locator("#edit-original")).to_contain_text("QA_USER_A")
    expect(card(page, result["id"])).to_contain_text("QA_USER_A")
    page.locator("#refresh-data").click()
    ready(page)
    expect(day(page, "2026-12-05")).to_contain_text("예약")
    expect(page.locator("#name")).to_have_value("QA_DRAFT")
    assert db_snapshot(admin_server) == before


def test_tc007_08_logs_pages_settings_and_existing_occupancy(page, admin_server, admin_proxy):
    for value in range(2, 15):
        create(admin_server, [f"2026-12-{value:02d}"])
    open_admin(page, admin_proxy)
    ids = page.locator("#logs li[data-id]").evaluate_all("els => els.map(el => Number(el.dataset.id))")
    assert ids == list(range(13, 3, -1))
    page.locator("#logs-next").click()
    ready(page)
    expect(page.locator("#logs-page")).to_contain_text("2")
    assert page.locator("#logs li[data-id]").evaluate_all("els => els.map(el => Number(el.dataset.id))") == [3, 2, 1]
    expect(page.locator("#logs-next")).to_be_disabled()
    page.locator("#logs-prev").click()
    ready(page)
    assert page.locator("#logs li[data-id]").evaluate_all("els => els.map(el => Number(el.dataset.id))") == ids
    before = db_snapshot(admin_server)
    page.locator("#booking-horizon").fill("2")
    submit(page, "PATCH", "/api/admin/settings", "#save-settings", 200)
    expect(page.locator("#current-horizon")).to_contain_text("2")
    after = db_snapshot(admin_server)
    assert after["reservations"] == before["reservations"]
    assert after["reserved_dates"] == before["reserved_dates"]
    expect(page.locator("#logs")).to_contain_text("SETTINGS")
    sent = writes(page)
    page.locator("#booking-horizon").fill("0")
    page.locator("#save-settings").click()
    assert sent == []  # native input validation
    assert db_snapshot(admin_server) == after


@pytest.mark.parametrize("failure", ["expired", "access"])
def test_tc007_09_real_security_rejection_and_explicit_recovery(page, admin_server, admin_proxy, failure):
    result = create(admin_server, ["2026-12-02"])
    open_admin(page, admin_proxy)
    card(page, result["id"]).get_by_role("button", name="수정", exact=True).click()
    page.locator("#memo").fill("QA_UNSAVED")
    before = db_snapshot(admin_server)
    sent = writes(page)
    if failure == "expired":
        admin_server.clock_file.write_text("1800")
    else:
        admin_server.access_file.write_text("deny")
    submit(page, "PATCH", f"/api/admin/reservations/{result['id']}", status=403)
    expect(page.locator("#status")).to_contain_text("403")
    expect(page.locator("#memo")).to_have_value("QA_UNSAVED")
    expect(page.locator("#save-reservation")).to_be_disabled()
    assert len(sent) == 1
    assert db_snapshot(admin_server) == before
    admin_server.access_file.write_text("allow")
    page.locator("#renew-csrf").click()
    expect(page.locator("#save-reservation")).to_be_enabled()
    assert len(sent) == 1
    submit(page, "PATCH", f"/api/admin/reservations/{result['id']}", status=200)
    assert len(sent) == 2


@pytest.mark.parametrize("failure", [422, 429, 503, "network"])
def test_tc007_10_failure_preserves_inputs_and_restores_controls(page, admin_server, admin_proxy, failure):
    open_admin(page, admin_proxy)
    fill(page)
    before = db_snapshot(admin_server)
    sent = writes(page)
    def fail(route):
        if failure == "network":
            route.abort("failed")
        else:
            route.fulfill(status=failure, json={"code": "TEST_FAILURE", "message": "QA_ERROR"}, headers={"Cache-Control": "no-store"})
    page.route("**/api/admin/reservations", fail)
    page.locator("#save-reservation").click()
    ready(page)
    expect(page.locator("#status")).to_contain_text("처리 결과" if failure == "network" else str(failure))
    expect(page.locator("#name")).to_have_value("QA_USER_A")
    expect(page.locator("#memo")).to_have_value("QA_MEMO_SENTINEL")
    expect(page.locator("#dates")).to_have_value("2026-12-02\n2026-12-03")
    if failure == "network":
        expect(page.locator("#save-reservation")).to_be_disabled()
        page.locator("#refresh-data").click()
        ready(page)
    expect(page.locator("#save-reservation")).to_be_enabled()
    assert len(sent) == 1
    assert db_snapshot(admin_server) == before


def test_tc007_10_pending_write_is_single_and_response_loss_is_not_retried(page, admin_server, admin_proxy):
    open_admin(page, admin_proxy)
    fill(page)
    pending = []
    sent = writes(page)
    page.route("**/api/admin/reservations", lambda route: pending.append(route))
    page.locator("#save-reservation").click()
    expect(page.locator("#save-reservation")).to_be_disabled()
    expect(page.locator("#name")).to_be_disabled()
    page.locator("#reservation-form").evaluate("form => {form.requestSubmit(); form.requestSubmit();}")
    page.wait_for_timeout(100)
    assert len(pending) == 1
    response = admin_proxy(pending[0].request)
    assert response.status_code == 201
    pending[0].abort("failed")
    ready(page)
    expect(page.locator("#status")).to_contain_text("결과")
    expect(page.locator("#name")).to_have_value("QA_USER_A")
    page.wait_for_timeout(100)
    assert len(sent) == 1
    state = db_snapshot(admin_server)
    assert len(state["reservations"]) == 1
    assert len(state["reservation_logs"]) == 1
    page.locator("#refresh-data").click()
    ready(page)
    expect(page.locator("#reservation-list")).to_contain_text("QA_USER_A")
    assert len(sent) == 1


def test_tc007_11_safe_rendering_private_cache_and_no_persistence(page, admin_server, admin_proxy):
    payload = "<img src=x onerror=window.pwned=1>"
    result = create(admin_server, ["2026-12-02"], name=payload, memo="<script>window.pwned=1</script>")
    # The production log serializer allowlists dates. Injected unrecognized data
    # must be excluded before the browser receives the audit record.
    with write_transaction(admin_server.db_path) as connection:
        connection.execute("UPDATE reservation_logs SET detail=?", (json.dumps({
            "before": [], "after": ["2026-12-02"], "memo": payload,
        }),))
    responses = []
    page.on("response", lambda response: responses.append(response) if "/api/admin/" in response.url else None)
    open_admin(page, admin_proxy)
    expect(card(page, result["id"])).to_contain_text(payload)
    card(page, result["id"]).get_by_role("button", name="수정", exact=True).click()
    expect(page.locator("#name")).to_have_value(payload)
    assert page.evaluate("window.pwned") is None
    assert page.locator("#reservation-list img, #reservation-list script, #logs img, #logs script").count() == 0
    assert payload not in page.locator("#logs").inner_text()
    for response in responses:
        assert response.headers["cache-control"] == "no-store"
        if not response.url.endswith("/csrf"):
            body = response.text()
            assert "cancel_token" not in body
            assert all(value not in body for value in admin_server.sensitive_values if "." in value and len(value) > 100)
    assert page.evaluate("({local: Object.keys(localStorage), session: Object.keys(sessionStorage)})") == {"local": [], "session": []}
    assert page.evaluate("caches.keys()") == []
    assert page.context.cookies() == []
    assert page.url == "https://admin.test/admin"
    assert all(value not in page.content() for value in admin_server.sensitive_values if "." in value and len(value) > 100)
    page.goto("https://admin.test/")
    assert page.locator("a[href*='admin']").count() == 0
    assert payload not in page.locator("body").inner_text()


@pytest.mark.parametrize("viewport", [(360, 800), (390, 844), (1280, 800)])
def test_tc007_12_mobile_touch_keyboard_focus_and_overflow(page, admin_server, admin_proxy, viewport):
    result = create(admin_server, ["2026-12-02"], name="가" * 40, memo="M" * 200)
    page.set_viewport_size({"width": viewport[0], "height": viewport[1]})
    open_admin(page, admin_proxy)
    for selector in ("#reservation-list", "#reservation-form", "#block-form", "#logs", "#settings-form"):
        page.locator(selector).scroll_into_view_if_needed()
        expect(page.locator(selector)).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    card(page, result["id"]).get_by_role("button", name="수정", exact=True).tap()
    expect(page.locator("#name")).to_be_focused()
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.tagName") in {"INPUT", "TEXTAREA", "BUTTON"}
    for selector in ("#save-reservation", "#save-block", "#save-settings", "#refresh-data"):
        button = page.locator(selector)
        button.scroll_into_view_if_needed()
        box = button.bounding_box()
        assert box["width"] >= 44 and box["height"] >= 44
    page.locator("#next-month").focus()
    page.keyboard.press("Enter")
    ready(page)
    expect(day(page, "2027-01-02")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")


def test_tc007_10_successful_write_failed_refresh_is_reported_as_committed(page, admin_server, admin_proxy):
    open_admin(page, admin_proxy)
    fill(page)
    sent = writes(page)
    def fail_refresh(route):
        route.fulfill(status=503, json={"code": "DATABASE_BUSY", "message": "QA_ERROR"}, headers={"Cache-Control": "no-store"})
    page.route("**/api/admin/logs?*", fail_refresh)
    submit(page)
    expect(page.locator("#status")).to_contain_text("변경은 완료했지만 조회에 실패")
    expect(page.locator("#save-reservation")).to_be_disabled()
    assert len(sent) == 1
    state = db_snapshot(admin_server)
    assert len(state["reservations"]) == 1
    assert len(state["reservation_logs"]) == 1
    page.unroute("**/api/admin/logs?*", fail_refresh)
    page.locator("#refresh-data").click()
    ready(page)
    expect(page.locator("#reservation-list")).to_contain_text("QA_USER_A")
    expect(page.locator("#save-reservation")).to_be_enabled()
    assert len(sent) == 1
    assert db_snapshot(admin_server) == state


def test_tc007_10_month_loading_serializes_navigation_and_keeps_full_draft(page, admin_server, admin_proxy):
    open_admin(page, admin_proxy)
    fill(page, dates="2026-12-31\n2027-01-02")
    pending = []
    page.route("**/api/calendar?month=2027-01", lambda route: pending.append(route))
    page.locator("#next-month").click()
    expect(page.locator("#next-month")).to_be_disabled()
    expect(page.locator("#prev-month")).to_be_disabled()
    page.locator("#next-month").dispatch_event("click")
    page.locator("#prev-month").dispatch_event("click")
    page.wait_for_timeout(100)
    assert len(pending) == 1
    pending[0].fallback()
    ready(page)
    expect(page.locator("#month-title")).to_have_attribute("data-month", "2027-01")
    expect(page.locator("#dates")).to_have_value("2026-12-31\n2027-01-02")
    expect(day(page, "2027-01-02")).to_have_attribute("aria-pressed", "true")
    assert db_snapshot(admin_server)["reservations"] == []


def test_tc007_11_log_response_text_is_never_interpreted_as_html(page, admin_server, admin_proxy):
    create(admin_server, ["2026-12-02"])
    payload = "<img src=x onerror=window.pwned=1>"
    def malicious_log(route):
        # Production excludes unknown audit fields/actions. Deliberate malformed
        # response tests the rendering boundary as a separate defense layer.
        response = admin_proxy(route.request)
        result = response.json()
        result["logs"][0]["action"] = payload
        result["logs"][0]["detail"]["extra"] = "<script>window.pwned=1</script>"
        route.fulfill(status=response.status_code, json=result, headers={"Cache-Control": "no-store"})
    page.route("**/api/admin/logs?*", malicious_log)
    open_admin(page, admin_proxy)
    expect(page.locator("#logs")).to_contain_text(payload)
    expect(page.locator("#logs")).to_contain_text("<script>window.pwned=1</script>")
    assert page.locator("#logs img, #logs script").count() == 0
    assert page.evaluate("window.pwned") is None


@pytest.mark.parametrize("pending_path", ["csrf", "reservations?month=2026-12&status=ALL"])
def test_tc007_11_pagehide_discards_delayed_private_responses(page, admin_server, admin_proxy, pending_path):
    create(admin_server, ["2026-12-02"])
    pending = []
    page.route("https://admin.test/api/admin/" + pending_path, lambda route: pending.append(route))
    page.goto("https://admin.test/admin")
    expect(page.locator("#refresh-data")).to_be_disabled()
    page.wait_for_timeout(100)
    assert len(pending) == 1
    # Read the real response before hide, then deliver it only after the document
    # disposed its private state. A late completion must never restore that state.
    response = admin_proxy(pending[0].request)
    assert response.status_code == 200
    page.evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide'))")
    pending[0].fulfill(status=200, headers=dict(response.headers), body=response.content)
    page.wait_for_timeout(150)
    expect(page.locator("#reservation-list")).to_be_empty()
    expect(page.locator("#logs")).to_be_empty()
    expect(page.locator("#status")).to_be_empty()
    expect(page.locator("#name")).to_have_value("")
    expect(page.locator("#dates")).to_have_value("")
    expect(page.locator("#save-reservation")).to_be_disabled()
    assert all(value not in page.content() for value in admin_server.sensitive_values if "." in value and len(value) > 100)


@pytest.mark.parametrize("operation", ["edit", "cancel", "block", "unblock", "settings"])
@pytest.mark.parametrize("failure", [422, 429, 503, "network"])
def test_tc007_10_each_management_mutation_preserves_state_on_failure(page, admin_server, admin_proxy, operation, failure):
    result = create(admin_server, ["2026-12-02"])
    block_dates(admin_server.db_path, dates=["2026-12-03"], clock=lambda: NOW)
    open_admin(page, admin_proxy)
    if operation == "edit":
        card(page, result["id"]).get_by_role("button", name="수정", exact=True).click()
        page.locator("#memo").fill("QA_DRAFT")
        path, selector = f"/api/admin/reservations/{result['id']}", "#save-reservation"
    elif operation == "cancel":
        path, selector = f"/api/admin/reservations/{result['id']}", None
    elif operation == "block":
        page.locator("#block-dates").fill("2026-12-04")
        page.locator("#block-memo").fill("QA_DRAFT")
        path, selector = "/api/admin/blocked-dates", "#save-block"
    elif operation == "unblock":
        path, selector = "/api/admin/blocked-dates/2026-12-03", "#blocked-list button[data-date='2026-12-03']"
    else:
        page.locator("#booking-horizon").fill("30")
        path, selector = "/api/admin/settings", "#save-settings"
    before = db_snapshot(admin_server)
    inputs_before = page.locator("input, textarea").evaluate_all("els => els.map(el => [el.id, el.value])")
    sent = writes(page)
    def fail(route):
        if route.request.method == "GET":
            route.fallback()
        elif failure == "network":
            route.abort("failed")
        else:
            route.fulfill(status=failure, json={"code": "TEST_FAILURE", "message": "QA_ERROR"}, headers={"Cache-Control": "no-store"})
    page.route("https://admin.test" + path, fail)
    if operation in {"cancel", "unblock"}:
        page.once("dialog", lambda dialog: dialog.accept())
    if selector:
        page.locator(selector).click()
    else:
        card(page, result["id"]).get_by_role("button", name="예약 취소", exact=True).click()
    ready(page)
    expect(page.locator("#status")).to_contain_text("처리 결과" if failure == "network" else str(failure))
    assert page.locator("#status").get_attribute("data-kind") == "error"
    assert page.locator("input, textarea").evaluate_all("els => els.map(el => [el.id, el.value])") == inputs_before
    assert len(sent) == 1
    assert db_snapshot(admin_server) == before
    if failure == "network":
        expect(page.locator("#save-reservation")).to_be_disabled()
        page.locator("#refresh-data").click()
        ready(page)
    expect(page.locator("#save-reservation")).to_be_enabled()
    assert len(sent) == 1
    assert db_snapshot(admin_server) == before
