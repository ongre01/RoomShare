"""RS-005 server-rendered shell contracts; browser behavior is tested in e2e."""

from datetime import datetime, timezone
from html.parser import HTMLParser

from fastapi.testclient import TestClient
import pytest

from app.main import create_app


class PageElements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = []

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


@pytest.mark.parametrize(
    ("instant", "month"),
    [
        (datetime(2026, 11, 30, 14, 59, 59, tzinfo=timezone.utc), "2026-11"),
        (datetime(2026, 11, 30, 15, tzinfo=timezone.utc), "2026-12"),
        (datetime(2026, 12, 31, 15, tzinfo=timezone.utc), "2027-01"),
    ],
)
def test_tc005_03_initial_month_is_server_kst_without_database(settings, instant, month):
    with TestClient(create_app(settings, clock=lambda: instant)) as client:
        response = client.get("/")
    assert response.status_code == 200
    page = PageElements()
    page.feed(response.text)
    app = next(attrs for _, attrs in page.elements if attrs.get("id") == "calendar-app")
    assert app["data-initial-month"] == month
    assert not settings.db_path.exists()


def test_tc005_02_public_shell_has_only_local_assets_and_no_excluded_features(client):
    response = client.get("/")
    assert response.status_code == 200
    page = PageElements()
    page.feed(response.text)
    assert not [tag for tag, _ in page.elements if tag in {"img", "iframe"}]
    for _, attrs in page.elements:
        for key in ("href", "src", "action"):
            value = attrs.get(key, "")
            assert not any(part in value.lower() for part in ("admin", "login", "signup", "payment"))
            assert not value.startswith(("https://", "//"))
    assert not any(word in response.text for word in ("회원가입", "로그인", "결제", "가격"))
    scripts = [attrs for tag, attrs in page.elements if tag == "script"]
    assert scripts
    for script in scripts:
        assert "defer" in script
        asset = client.get(script["src"])
        assert asset.status_code == 200
        assert "javascript" in asset.headers["content-type"]


def test_tc005_11_page_no_store_and_credentials_never_server_rendered(client):
    response = client.get("/")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    page = PageElements()
    page.feed(response.text)
    for element_id in ("success-id", "success-token", "cancel-id", "cancel-token"):
        attrs = next(attrs for _, attrs in page.elements if attrs.get("id") == element_id)
        assert attrs.get("value", "") == ""
        assert attrs.get("autocomplete") == "off"
    for element_id in ("success-id", "success-token"):
        attrs = next(attrs for _, attrs in page.elements if attrs.get("id") == element_id)
        assert "readonly" in attrs


def test_tc005_12_form_controls_have_explicit_labels_and_live_messages(client):
    page = PageElements()
    page.feed(client.get("/").text)
    labels = {attrs.get("for") for tag, attrs in page.elements if tag == "label"}
    for element_id in ("name", "memo", "cancel-id", "cancel-token", "cancel-confirm"):
        assert element_id in labels
    for element_id in ("calendar-status", "reservation-status", "cancel-status", "copy-status"):
        attrs = next(attrs for _, attrs in page.elements if attrs.get("id") == element_id)
        assert attrs.get("aria-live") in {"polite", "assertive"}
