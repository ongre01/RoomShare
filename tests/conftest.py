"""RS-001 fixtures; RS-002 will add real migrations to the isolated DB path."""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    """Keep user configuration out of tests and restore the environment afterward."""
    for key in tuple(os.environ):
        if key.startswith("ROOMSHARE_"):
            monkeypatch.delenv(key)
    values = {
        "ROOMSHARE_DB_PATH": str(tmp_path / "environment" / "test.db"),
        "ROOMSHARE_TIMEZONE": "Asia/Seoul",
        "ROOMSHARE_PUBLIC_HOST": "public.test",
        "ROOMSHARE_ADMIN_HOST": "admin.test",
        "ROOMSHARE_BOOKING_HORIZON_DAYS": "90",
        "ROOMSHARE_ADMIN_ACCESS": "deny",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return values


@pytest.fixture
def db_path(tmp_path) -> Path:
    # Intentionally not created until the RS-002 migration fixture exists.
    return tmp_path / "database" / "test.db"


@pytest.fixture
def settings(db_path):
    return Settings(
        db_path=db_path,
        public_host="public.test",
        admin_host="admin.test",
    )


@pytest.fixture
def app(settings):
    return create_app(settings=settings)


@pytest.fixture
def client(app, settings):
    with TestClient(app, base_url=f"http://{settings.public_host}") as client:
        yield client
