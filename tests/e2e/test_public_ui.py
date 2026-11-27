"""TC-005-01..12: browser observations backed by real HTTP/SQLite.

Only deliberate timing, malformed responses and transport failures use routes.
No trace/video is retained because those artifacts could capture cancel tokens.
"""

from contextlib import closing
import json
import re

import httpx
from playwright.sync_api import expect
import pytest

from app.db import connect, write_transaction
from tests.db_helpers import snapshot


def open_calendar(page, server):
    response = page.goto(server.url)
    assert response.status == 200
    expect(day(page, "2026-12-02")).to_be_visible()
    expect(page.locator("#month-title")).to_have_attribute("data-month", "2026-12")


def day(page, date):
    return page.locator(f"#calendar-grid button[data-date='{date}']")


def selected(page):
    return page.locator("#selected-dates").inner_text()


def choose(page, *dates):
    for date in dates:
        day(page, date).click()


def fill_booking(page, *, name="QA_USER_A", memo="QA_MEMO_SENTINEL"):
    page.locator("#name").fill(name)
    page.locator("#memo").fill(memo)


def create_api(server, dates):
    with httpx.Client(base_url=server.url, trust_env=False) as client:
        response = client.post("/api/reservations", json={"name": "QA_USER_B", "dates": dates})
    assert response.status_code == 201
    result = response.json()
    server.sensitive_values.append(result["cancel_token"])
    return result


def db_snapshot(server):
    with closing(connect(server.db_path)) as connection:
        return snapshot(connection)


def submit_booking(page, server):
    with page.expect_response(
        lambda response: response.url.endswith("/api/reservations")
        and response.request.method == "POST"
    ) as response_info:
        page.locator("#reserve-button").click()
    response = response_info.value
    assert response.status == 201
    assert response.headers["cache-control"] == "no-store"
    expect(page.locator("#reservation-success")).to_be_visible()
    result = response.json()
    server.sensitive_values.append(result["cancel_token"])
    assert page.locator("#success-id").input_value() == result["id"]
    # Avoid token values in assertion rewriting/failure output.
    assert bool(page.locator("#success-token").input_value() == result["cancel_token"])
    return result


@pytest.mark.parametrize("viewport", [(360, 800), (390, 844), (1280, 800)])
def test_tc005_01_viewports_and_main_controls(page, live_server, viewport):
    page.set_viewport_size({"width": viewport[0], "height": viewport[1]})
    open_calendar(page, live_server)
    day(page, "2026-12-02").tap()
    expect(page.locator("#selected-count")).to_have_text("1")
    fill_booking(page)
    submit_booking(page, live_server)
    for selector in ("#copy-reservation", "#dismiss-success", "#cancel-button"):
        element = page.locator(selector)
        element.scroll_into_view_if_needed()
        expect(element).to_be_visible()
        box = element.bounding_box()
        assert box["width"] >= 44 and box["height"] >= 44
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    page.locator("#dismiss-success").click()
    page.locator("#next-month").tap()
    expect(day(page, "2027-01-02")).to_be_visible()
    page.locator("#today-month").tap()
    expect(day(page, "2026-12-02")).to_be_visible()


def test_tc005_02_excluded_features_in_rendered_dom(page, live_server):
    open_calendar(page, live_server)
    assert page.locator("img, video, iframe, a[href*='admin'], form[action*='login']").count() == 0
    assert not re.search(r"로그인|회원가입|결제|가격|관리자", page.locator("body").inner_text())
    urls = page.locator("script[src], link[rel='stylesheet']").evaluate_all(
        "elements => elements.map(el => el.src || el.href)"
    )
    assert all(url.startswith(live_server.url + "/") for url in urls)


def test_tc005_03_multimonth_sorted_selection_and_removal(page, live_server):
    open_calendar(page, live_server)
    choose(page, "2026-12-03", "2026-12-02")
    page.locator("#next-month").click()
    choose(page, "2027-01-01")
    expect(page.locator("#selected-count")).to_have_text("3")
    assert selected(page).index("2026-12-02") < selected(page).index("2026-12-03") < selected(page).index("2027-01-01")
    page.locator("#prev-month").click()
    expect(day(page, "2026-12-02")).to_have_attribute("aria-pressed", "true")
    day(page, "2026-12-03").click()
    expect(page.locator("#selected-count")).to_have_text("2")
    page.get_by_role("button", name="2027-01-01 선택 해제", exact=True).click()
    expect(page.locator("#selected-count")).to_have_text("1")
    page.locator("#clear-selection").click()
    expect(page.locator("#selected-count")).to_have_text("0")


def test_tc005_03_today_follows_fresh_server_month_after_kst_rollover(page, live_server):
    open_calendar(page, live_server)
    choose(page, "2026-12-02")
    def rollover(route):
        response = route.fetch()
        calendar = response.json()
        calendar.update(today="2027-01-01", min_date="2027-01-01", max_date="2027-03-31")
        for item in calendar["dates"]:
            if item["date"] < "2027-01-01":
                item["status"] = "PAST"
        route.fulfill(response=response, json=calendar)
    page.route("**/api/calendar?*", rollover)
    page.locator("#today-month").click()
    expect(page.locator("#month-title")).to_have_attribute("data-month", "2027-01")
    expect(day(page, "2027-01-01")).to_have_attribute("aria-current", "date")
    expect(page.locator("#selected-count")).to_have_text("0")


def test_tc005_04_forbidden_dates_even_available_outside_window(page, live_server):
    create_api(live_server, ["2026-12-02"])
    with write_transaction(live_server.db_path) as connection:
        connection.execute(
            "INSERT INTO reserved_dates(date, type) VALUES (?, ?)", ("2026-12-03", "BLOCKED"),
        )
    open_calendar(page, live_server)
    for date, label in [("2026-12-02", "예약"), ("2026-12-03", "불가")]:
        expect(day(page, date)).to_be_disabled()
        assert label in day(page, date).inner_text()
        assert date in day(page, date).get_attribute("aria-label")
        day(page, date).dispatch_event("click")
        day(page, date).evaluate("element => element.focus()")
        page.keyboard.press("Enter")
        page.keyboard.press("Space")
    page.locator("#prev-month").click()
    expect(day(page, "2026-11-30")).to_be_disabled()
    expect(day(page, "2026-11-30")).to_contain_text("과거")
    page.locator("#today-month").click()
    for _ in range(3):
        page.locator("#next-month").click()
    expect(day(page, "2027-03-01")).to_be_disabled()
    expect(day(page, "2027-03-01")).to_contain_text("기간")
    day(page, "2027-03-01").dispatch_event("click")
    expect(page.locator("#selected-count")).to_have_text("0")


def test_tc005_05_actual_booking_across_months_once_and_refresh(page, live_server):
    open_calendar(page, live_server)
    choose(page, "2026-12-02")
    page.locator("#next-month").click()
    choose(page, "2027-01-01")
    fill_booking(page)
    result = submit_booking(page, live_server)
    assert result["dates"] == ["2026-12-02", "2027-01-01"]
    expect(day(page, "2027-01-01")).to_be_disabled()
    expect(page.locator("#selected-count")).to_have_text("0")
    state = db_snapshot(live_server)
    assert len(state["reservations"]) == 1
    assert len(state["reserved_dates"]) == 2
    assert len(state["reservation_logs"]) == 1
    page.locator("#dismiss-success").click()
    expect(page.locator("#reservation-success")).to_be_hidden()
    assert page.locator("#success-token").input_value() == ""
    page.reload()
    expect(day(page, "2026-12-02")).to_be_disabled()
    expect(page.locator("#reservation-success")).to_be_hidden()
    assert page.locator("#success-token").input_value() == ""


def test_tc005_06_slow_request_disables_duplicate_submit(page, live_server):
    open_calendar(page, live_server)
    choose(page, "2026-12-02")
    fill_booking(page)
    pending = []
    page.route("**/api/reservations", lambda route: pending.append(route))
    page.locator("#reserve-button").click()
    expect(page.locator("#reserve-button")).to_be_disabled()
    page.locator("#reservation-form").evaluate("form => { form.requestSubmit(); form.requestSubmit(); }")
    page.wait_for_timeout(100)
    assert len(pending) == 1
    pending[0].continue_()
    expect(page.locator("#reservation-success")).to_be_visible()
    assert len(db_snapshot(live_server)["reservations"]) == 1


def test_tc005_07_actual_conflict_refreshes_all_related_months(page, live_server):
    open_calendar(page, live_server)
    choose(page, "2026-12-02", "2026-12-03")
    page.locator("#next-month").click()
    choose(page, "2027-01-01")
    create_api(live_server, ["2026-12-02"])
    before = db_snapshot(live_server)
    requests = []
    page.on("request", lambda request: requests.append((request.method, request.url)))
    fill_booking(page)
    page.locator("#reserve-button").click()
    expect(page.locator("#reservation-status")).to_contain_text("2026-12-02")
    expect(page.locator("#selected-count")).to_have_text("2")
    assert "2026-12-03" in selected(page) and "2027-01-01" in selected(page)
    assert "2026-12-02" not in selected(page)
    assert any("month=2026-12" in url for method, url in requests if method == "GET")
    assert any("month=2027-01" in url for method, url in requests if method == "GET")
    assert sum(method == "POST" for method, _ in requests) == 1
    assert db_snapshot(live_server) == before
    page.locator("#prev-month").click()
    expect(day(page, "2026-12-02")).to_be_disabled()
    expect(day(page, "2026-12-03")).to_have_attribute("aria-pressed", "true")


def test_tc005_07_failed_conflict_refresh_requires_successful_manual_refresh(page, live_server):
    open_calendar(page, live_server)
    choose(page, "2026-12-02", "2026-12-03")
    page.locator("#next-month").click()
    choose(page, "2027-01-01")
    create_api(live_server, ["2026-12-02"])
    page.route("**/api/calendar?month=2026-12", lambda route: route.abort("failed"))
    fill_booking(page)
    page.locator("#reserve-button").click()
    expect(page.locator("#calendar-status")).to_contain_text("갱신하지 못")
    expect(page.locator("#reserve-button")).to_be_disabled()
    expect(page.locator("#selected-count")).to_have_text("2")
    page.unroute("**/api/calendar?month=2026-12")
    page.locator("#refresh-calendar").click()
    expect(page.locator("#reserve-button")).to_be_enabled()
    expect(page.locator("#selected-count")).to_have_text("2")
    assert len(db_snapshot(live_server)["reservations"]) == 1


def test_tc005_08_reversed_month_response_cannot_overwrite_latest(page, live_server):
    open_calendar(page, live_server)
    choose(page, "2026-12-02")
    pending = []
    page.route("**/api/calendar?month=2026-12", lambda route: pending.append(route))
    page.locator("#next-month").click()
    expect(day(page, "2027-01-01")).to_be_visible()
    page.locator("#prev-month").click()
    page.locator("#next-month").click()
    expect(day(page, "2027-01-01")).to_be_visible()
    choose(page, "2027-01-01")
    assert len(pending) == 1
    # An old response reports the selected December date unavailable. If applied,
    # it would corrupt both the latest month's UI and the selected-date Set.
    with httpx.Client(base_url=live_server.url, trust_env=False) as client:
        stale = client.get("/api/calendar?month=2026-12").json()
    stale["dates"][1]["status"] = "RESERVED"
    pending[0].fulfill(status=200, content_type="application/json", body=json.dumps(stale))
    page.wait_for_timeout(100)
    expect(page.locator("#month-title")).to_have_attribute("data-month", "2027-01")
    expect(day(page, "2027-01-01")).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#selected-count")).to_have_text("2")


@pytest.mark.parametrize("status,word", [(422, "입력"), (429, "잠시"), (503, "잠시")])
def test_tc005_09_http_errors_preserve_input_and_never_retry(page, live_server, status, word):
    open_calendar(page, live_server)
    choose(page, "2026-12-02")
    fill_booking(page)
    requests = []
    def fail(route):
        requests.append(route.request.method)
        route.fulfill(status=status, content_type="application/json", body=json.dumps({"code": "TEST_ERROR", "message": "not trusted UI HTML"}))
    page.route("**/api/reservations", fail)
    before = db_snapshot(live_server)
    page.locator("#reserve-button").click()
    expect(page.locator("#reservation-status")).to_contain_text(word)
    expect(page.locator("#reservation-success")).to_be_hidden()
    expect(page.locator("#name")).to_have_value("QA_USER_A")
    expect(page.locator("#memo")).to_have_value("QA_MEMO_SENTINEL")
    expect(page.locator("#selected-count")).to_have_text("1")
    expect(page.locator("#reserve-button")).to_be_enabled()
    page.wait_for_timeout(100)
    assert requests == ["POST"]
    assert db_snapshot(live_server) == before


@pytest.mark.parametrize("committed", [False, True], ids=["connection-drop", "response-lost-after-commit"])
def test_tc005_09_transport_unknown_outcome_is_not_retried(page, live_server, committed):
    open_calendar(page, live_server)
    choose(page, "2026-12-02")
    fill_booking(page)
    requests = []
    def drop(route):
        requests.append(route.request.method)
        if committed:
            response = route.fetch()
            assert response.status == 201
            live_server.sensitive_values.append(response.json()["cancel_token"])
        route.abort("failed")
    page.route("**/api/reservations", drop)
    page.locator("#reserve-button").click()
    expect(page.locator("#reservation-status")).to_contain_text("확인")
    expect(page.locator("#reservation-success")).to_be_hidden()
    expect(page.locator("#name")).to_have_value("QA_USER_A")
    expect(page.locator("#memo")).to_have_value("QA_MEMO_SENTINEL")
    expect(page.locator("#selected-count")).to_have_text("1")
    page.wait_for_timeout(100)
    assert requests == ["POST"]
    assert len(db_snapshot(live_server)["reservations"]) == int(committed)


def test_tc005_09_client_validation_and_calendar_failure(page, live_server):
    open_calendar(page, live_server)
    requests = []
    page.on("request", lambda request: requests.append(request.method))
    page.locator("#reservation-form").evaluate("form => form.requestSubmit()")
    assert "POST" not in requests
    choose(page, "2026-12-02")
    page.locator("#name").fill("   ")
    page.locator("#reserve-button").click()
    expect(page.locator("#reservation-status")).to_contain_text("이름")
    assert "POST" not in requests
    page.route("**/api/calendar?*", lambda route: route.fulfill(status=503, content_type="application/json", body='{"code":"DATABASE_BUSY"}'))
    page.locator("#refresh-calendar").click()
    expect(page.locator("#calendar-status")).to_contain_text("갱신하지 못")
    expect(page.locator("#selected-count")).to_have_text("1")


def test_tc005_10_actual_cancellation_confirmation_invalid_and_valid(page, live_server):
    result = create_api(live_server, ["2026-12-02", "2027-01-01"])
    open_calendar(page, live_server)
    page.locator("#cancel-id").fill(result["id"])
    page.locator("#cancel-token").fill("invalid-test-token")
    before = db_snapshot(live_server)
    requests = []
    page.on("request", lambda request: requests.append(request.method))
    page.locator("#cancel-button").click()
    assert "POST" not in requests
    page.locator("#cancel-confirm").check()
    page.locator("#cancel-button").click()
    expect(page.locator("#cancel-status")).to_contain_text("확인")
    assert db_snapshot(live_server) == before
    expect(day(page, "2026-12-02")).to_be_disabled()
    page.locator("#cancel-token").fill(result["cancel_token"])
    page.locator("#cancel-confirm").check()
    page.locator("#cancel-button").click()
    expect(page.locator("#cancel-status")).to_contain_text("취소")
    expect(day(page, "2026-12-02")).to_be_enabled()
    state = db_snapshot(live_server)
    assert len(state["reserved_dates"]) == 0
    assert len(state["reservation_logs"]) == 2
    assert page.locator("#cancel-token").input_value() == ""


def test_tc005_10_cancel_failure_during_month_load_has_explicit_refresh_recovery(page, live_server):
    result = create_api(live_server, ["2026-12-02"])
    open_calendar(page, live_server)
    page.locator("#cancel-id").fill(result["id"])
    page.locator("#cancel-token").fill("invalid-test-token")
    page.locator("#cancel-confirm").check()
    pending = []
    page.route("**/api/calendar?month=2027-01", lambda route: pending.append(route))
    page.locator("#next-month").click()
    expect(page.locator("#calendar-status")).to_contain_text("불러오는 중")
    page.locator("#cancel-button").click()
    expect(page.locator("#cancel-status")).to_contain_text("확인")
    expect(page.locator("#calendar-status")).to_contain_text("새로고침")
    expect(page.locator("#calendar-grid")).to_have_attribute("aria-busy", "false")
    assert len(pending) == 1
    pending[0].continue_()
    page.wait_for_timeout(100)
    assert page.locator("#calendar-grid button").count() == 0
    page.unroute("**/api/calendar?month=2027-01")
    page.locator("#refresh-calendar").click()
    expect(day(page, "2027-01-01")).to_be_enabled()
    assert len(db_snapshot(live_server)["reserved_dates"]) == 1


def test_tc009_01_another_user_can_rebook_only_after_cancellation(page, live_server):
    dates = ["2026-12-02", "2026-12-03"]
    open_calendar(page, live_server)
    choose(page, *dates)
    fill_booking(page)
    first = submit_booking(page, live_server)
    page.locator("#dismiss-success").click()
    live_server.sensitive_values.extend(["QA_USER_A", "QA_MEMO_SENTINEL"])

    with httpx.Client(base_url=live_server.url, trust_env=False) as other:
        public = other.get("/api/calendar", params={"month": "2026-12"})
        assert public.status_code == 200 and public.headers["cache-control"] == "no-store"
        calendar = public.json()
        assert set(calendar) == {"dates", "today", "min_date", "max_date"}
        assert all(set(entry) == {"date", "status"} for entry in calendar["dates"])
        assert [entry["status"] for entry in calendar["dates"] if entry["date"] in dates] == ["RESERVED"] * 2
        assert not any(value in public.text for value in (*live_server.sensitive_values, first["id"]))
        body = {"name": "QA_USER_B", "dates": dates}
        denied = other.post("/api/reservations", json=body)
        assert denied.status_code == 409 and denied.json()["dates"] == dates

        page.locator("#cancel-id").fill(first["id"])
        page.locator("#cancel-token").fill(first["cancel_token"])
        page.locator("#cancel-confirm").check()
        page.locator("#cancel-button").click()
        for date in dates:
            expect(day(page, date)).to_be_enabled()
        second = other.post("/api/reservations", json=body)
        assert second.status_code == 201
        replacement = second.json()
        live_server.sensitive_values.append(replacement["cancel_token"])
    assert replacement["id"] != first["id"]
    with closing(connect(live_server.db_path)) as db:
        assert dict(db.execute("SELECT id,status FROM reservations")) == {
            first["id"]: "CANCELLED", replacement["id"]: "CONFIRMED",
        }
        assert dict(db.execute("SELECT date,reservation_id FROM reserved_dates")) == dict.fromkeys(dates, replacement["id"])
        assert [row[0] for row in db.execute("SELECT action FROM reservation_logs ORDER BY id")] == [
            "CREATE", "CANCEL", "CREATE",
        ]


def test_tc005_11_xss_token_privacy_storage_url_cache_and_copy_fallback(page, live_server):
    page.add_init_script("""
        Object.defineProperty(navigator, 'clipboard', {configurable: true,
            value: {writeText: () => Promise.reject(new Error('TEST_DENIED'))}});
    """)
    logs, requests = [], []
    page.on("console", lambda message: logs.append(message.text))
    page.on("request", lambda request: requests.append(request.url))
    open_calendar(page, live_server)
    choose(page, "2026-12-02")
    malicious_name = '<svg onload="window.__xss=1">'
    malicious_memo = '<img src=x onerror="window.__xss=2">'
    fill_booking(page, name=malicious_name, memo=malicious_memo)
    live_server.sensitive_values.extend([malicious_name, malicious_memo])
    result = submit_booking(page, live_server)
    assert page.evaluate("window.__xss === undefined")
    assert page.locator("svg, img").count() == 0
    assert page.evaluate("Object.keys(localStorage)") == []
    assert page.evaluate("Object.keys(sessionStorage)") == []
    assert page.evaluate("caches.keys()") == []
    assert page.url == live_server.url + "/"
    assert not any(result["cancel_token"] in value for value in logs + requests)
    assert page.locator("#success-token").get_attribute("readonly") is not None
    page.locator("#copy-reservation").click()
    expect(page.locator("#copy-status")).to_contain_text("직접")
    page.locator("#dismiss-success").click()
    assert page.locator("#success-token").input_value() == ""


def test_tc005_11_copy_success_only_after_user_action_and_history_clears_token(page, live_server):
    page.add_init_script("""
        window.__copied = [];
        Object.defineProperty(navigator, 'clipboard', {configurable: true,
            value: {writeText: async value => {window.__copied.push(value);}}});
    """)
    open_calendar(page, live_server)
    choose(page, "2026-12-02")
    fill_booking(page)
    result = submit_booking(page, live_server)
    assert page.evaluate("window.__copied.length") == 0
    page.locator("#copy-reservation").click()
    expect(page.locator("#copy-status")).to_contain_text("복사했습니다")
    assert page.evaluate("window.__copied.length") == 1
    copied = page.evaluate("window.__copied[0]")
    assert bool(result["id"] in copied and result["cancel_token"] in copied)
    page.goto(live_server.url + "/healthz")
    page.go_back()
    expect(day(page, "2026-12-02")).to_be_disabled()
    expect(page.locator("#reservation-success")).to_be_hidden()
    assert page.locator("#success-token").input_value() == ""


def test_tc005_12_keyboard_focus_labels_live_status_and_disabled_dates(page, live_server):
    create_api(live_server, ["2026-12-03"])
    open_calendar(page, live_server)
    for selector in ("#calendar-status", "#reservation-status", "#cancel-status"):
        assert page.locator(selector).get_attribute("aria-live") in ("polite", "assertive")
    for identifier in ("name", "memo", "cancel-id", "cancel-token", "cancel-confirm"):
        assert page.locator(f"label[for='{identifier}']").count() == 1
    def reach_by_keyboard(target, key="Tab"):
        # Read focus only: all movement must use actual browser keyboard events.
        for _ in range(100):
            if target.evaluate("element => element === document.activeElement"):
                return
            page.keyboard.press(key)
        raise AssertionError("Expected control is unreachable by keyboard")
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement !== document.body")
    reach_by_keyboard(day(page, "2026-12-02"))
    assert day(page, "2026-12-02").evaluate("el => getComputedStyle(el).outlineStyle !== 'none'")
    page.keyboard.press("Space")
    expect(day(page, "2026-12-02")).to_have_attribute("aria-pressed", "true")
    assert page.evaluate("document.activeElement.dataset.date") == "2026-12-02"
    page.keyboard.press("Enter")
    expect(day(page, "2026-12-02")).to_have_attribute("aria-pressed", "false")
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.dataset.date") == "2026-12-04"
    expect(day(page, "2026-12-03")).to_be_disabled()
    reach_by_keyboard(page.locator("#next-month"), "Shift+Tab")
    page.keyboard.press("Enter")
    expect(day(page, "2027-01-01")).to_be_visible()
