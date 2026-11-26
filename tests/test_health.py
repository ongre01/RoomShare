"""TC-001-05..08 and TC-001-10: bootstrap API and fixture isolation."""

from dataclasses import replace
from html.parser import HTMLParser
import os
from pathlib import Path
import subprocess
import sys

from fastapi.testclient import TestClient
import pytest

from app.config import load_settings
from app.main import create_app


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_tc001_05_import_has_no_database_or_network_side_effects(tmp_path):
    probe = """
import os
from pathlib import Path
import sys

calls = []
def audit(event, arguments):
    if event in {'sqlite3.connect', 'socket.connect', 'socket.getaddrinfo'}:
        calls.append(event)
        raise AssertionError('Unexpected external side effect: ' + event)
sys.addaudithook(audit)
sys.path.insert(0, sys.argv[1])
import app
import app.config
import app.main
assert calls == [], calls
assert not Path(os.environ['ROOMSHARE_DB_PATH']).parent.exists()
"""
    env = dict(os.environ)
    env["ROOMSHARE_DB_PATH"] = str(tmp_path / "unused" / "import.db")
    # An import must not read or validate runtime configuration either.
    env["ROOMSHARE_BOOKING_HORIZON_DAYS"] = "invalid-at-import"
    result = subprocess.run(
        [sys.executable, "-c", probe, str(PROJECT_ROOT)],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "unused").exists()


def test_tc001_05_factory_and_lifespan_do_not_create_database(settings):
    app = create_app(settings=settings)
    assert not settings.db_path.parent.exists()
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert not settings.db_path.parent.exists()
    assert not settings.db_path.parent.exists()


def test_tc001_06_health_is_minimal_and_repeatable(client, settings, monkeypatch):
    monkeypatch.setenv("QA_SECRET", "QA_SECRET_SENTINEL")
    for _ in range(3):
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/json"
        assert response.json() == {"status": "ok"}
        assert str(settings.db_path) not in response.text
        assert "QA_SECRET_SENTINEL" not in response.text
        assert "traceback" not in response.text.lower()
    assert not settings.db_path.exists()


@pytest.mark.parametrize(
    "path", ["/admin", "/admin/", "/api/admin", "/api/admin/reservations", "/api/admin/settings"],
)
@pytest.mark.parametrize("host", ["public.test", "admin.test"])
def test_tc001_07_initial_admin_routes_are_unavailable(client, path, host):
    response = client.get(
        path,
        headers={"host": host, "x-admin-access": "allow", "x-ssl-client-verify": "SUCCESS"},
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


class PageMetadata(HTMLParser):
    def __init__(self):
        super().__init__()
        self.viewport = None
        self.stylesheet = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "meta" and attributes.get("name") == "viewport":
            self.viewport = attributes.get("content")
        if tag == "link" and attributes.get("rel") == "stylesheet":
            self.stylesheet = attributes.get("href")


def test_tc001_08_templates_and_static_work_outside_project(settings, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with TestClient(create_app(settings=settings), base_url="http://public.test") as client:
        response = client.get("/")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        page = PageMetadata()
        page.feed(response.text)
        assert page.viewport is not None
        assert "width=device-width" in page.viewport
        assert "initial-scale=1" in page.viewport
        assert page.stylesheet is not None
        stylesheet = client.get(page.stylesheet)
        assert stylesheet.status_code == 200
        assert stylesheet.headers["content-type"].startswith("text/css")
        assert stylesheet.text.strip()
        assert str(PROJECT_ROOT) not in response.text
        assert str(tmp_path) not in response.text


def test_tc001_10_apps_and_environment_are_isolated(settings, tmp_path, monkeypatch):
    first = settings
    second = replace(
        settings,
        db_path=tmp_path / "second" / "test.db",
        public_host="second.test",
        admin_host="admin-second.test",
        booking_horizon_days=30,
    )
    before = dict(os.environ)
    app_one = create_app(settings=first)
    with TestClient(app_one) as client_one:
        assert client_one.get("/healthz").json() == {"status": "ok"}
    app_two = create_app(settings=second)
    with TestClient(app_two) as client_two:
        assert client_two.get("/healthz").json() == {"status": "ok"}
    assert app_one.state.settings is first
    assert app_two.state.settings is second
    assert app_one.state.settings.booking_horizon_days == 90
    assert app_two.state.settings.booking_horizon_days == 30
    assert app_one.state.settings.db_path != app_two.state.settings.db_path
    assert not first.db_path.parent.exists()
    assert not second.db_path.parent.exists()
    assert dict(os.environ) == before
    with monkeypatch.context() as patch:
        patch.setenv("ROOMSHARE_BOOKING_HORIZON_DAYS", "15")
        assert load_settings(env_file=None).booking_horizon_days == 15
    assert load_settings(env_file=None).booking_horizon_days == 90
    assert dict(os.environ) == before
