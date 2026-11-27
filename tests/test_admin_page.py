"""RS-007 protected HTML boundary, server clock and static asset contracts."""

from contextlib import closing
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser

from fastapi.testclient import TestClient
import pytest

from app.db import connect
from app.main import create_app
from app.security import AdminContext, resolve_admin_context
from app.services.reservations import create_reservation
from tests.db_helpers import snapshot


NOW = datetime(2026, 11, 30, 15, tzinfo=timezone.utc)


class Elements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.items = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.items.append((tag, dict(attrs)))


@pytest.mark.parametrize("host", ["public.test", "admin.test"])
@pytest.mark.parametrize("verified", [False, True])
def test_tc007_01_page_requires_exact_host_and_verified_context(settings, migrated_db, host, verified):
    booking = create_reservation(migrated_db, name="QA_PRIVATE_NAME", memo="QA_PRIVATE_MEMO",
                                 dates=["2026-12-02"], clock=lambda: NOW)
    app = create_app(settings, clock=lambda: NOW)
    if verified:
        app.dependency_overrides[resolve_admin_context] = lambda request: AdminContext()
    with closing(connect(migrated_db)) as connection:
        before = snapshot(connection)
    with TestClient(app, base_url=f"https://{host}") as client:
        response = client.get("/admin")
        csrf = client.get("/api/admin/csrf")
        allowed = host == "admin.test" and verified
        assert response.status_code == (200 if allowed else 403)
        assert csrf.status_code == (200 if allowed else 403)
        assert response.headers["cache-control"] == csrf.headers["cache-control"] == "no-store"
        if allowed:
            assert response.headers["content-type"].startswith("text/html")
            assert response.headers["referrer-policy"] == "no-referrer"
            assert csrf.json()["expires_in"] == 1800
            assert csrf.json()["csrf_token"] not in response.text
        else:
            assert response.json()["code"] == csrf.json()["code"] == "ADMIN_FORBIDDEN"
            assert "csrf_token" not in csrf.text
        sensitive = ["QA_PRIVATE_NAME", "QA_PRIVATE_MEMO", booking["id"], booking["cancel_token"],
                     hashlib.sha256(booking["cancel_token"].encode()).hexdigest()]
        for asset in ("/static/css/admin.css", "/static/js/admin.js"):
            static = client.get(asset)
            assert static.status_code == 200
            assert all(value not in static.text for value in sensitive)
        assert all(value not in response.text + csrf.text for value in sensitive)
    with closing(connect(migrated_db)) as connection:
        assert snapshot(connection) == before


@pytest.mark.parametrize("instant,month", [
    (datetime(2026, 11, 30, 14, 59, 59, tzinfo=timezone.utc), "2026-11"),
    (NOW, "2026-12"),
    (datetime(2026, 12, 31, 15, tzinfo=timezone.utc), "2027-01"),
])
def test_tc007_02_page_uses_kst_without_opening_database(settings, instant, month):
    app = create_app(settings, clock=lambda: instant)
    app.dependency_overrides[resolve_admin_context] = lambda request: AdminContext()
    with TestClient(app, base_url="https://admin.test") as client:
        response = client.get("/admin")
    assert response.status_code == 200
    elements = Elements(response.text).items
    initial = [attrs["data-initial-month"] for _, attrs in elements if "data-initial-month" in attrs]
    assert initial == [month]
    assert not settings.db_path.exists()


def test_tc007_01_11_shell_assets_are_same_origin_and_public_page_has_no_admin_link(settings):
    app = create_app(settings, clock=lambda: NOW)
    app.dependency_overrides[resolve_admin_context] = lambda request: AdminContext()
    # Simulate a TLS-terminating proxy: the backend request scheme is HTTP.
    with TestClient(app, base_url="http://admin.test") as client:
        response = client.get("/admin")
        assert response.status_code == 200
        elements = Elements(response.text).items
        assets = [attrs for tag, attrs in elements if tag in ("script", "link")]
        assert assets
        for attrs in assets:
            path = attrs.get("src", attrs.get("href", ""))
            assert path.startswith("/static/") and not path.startswith("//")
            assert client.get(path).status_code == 200
            if "src" in attrs:
                assert "defer" in attrs
        assert any(tag == "noscript" for tag, _ in elements)
        for tag, attrs in elements:
            if tag in {"input", "textarea", "select"}:
                assert attrs.get("name") not in {"csrf_token", "cancel_token", "proxy_secret"}
        public = client.get("https://public.test/")
        for _, attrs in Elements(public.text).items:
            for key in ("href", "src", "action"):
                assert "admin" not in attrs.get(key, "").lower()
        assert not settings.db_path.exists()
