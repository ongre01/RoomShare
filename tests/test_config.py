"""TC-001-01 through TC-001-04: configuration and startup validation."""

from dataclasses import FrozenInstanceError
import os
from pathlib import Path
import subprocess
import sys

import pytest

from app.config import Settings, SettingsError, load_settings
from app.main import create_app


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_tc001_01_valid_settings_are_normalized(db_path):
    settings = Settings(
        db_path=str(db_path),
        public_host=" PUBLIC.TEST. ",
        admin_host=" ADMIN.TEST. ",
        booking_horizon_days="90",
    )
    assert settings.db_path == db_path
    assert settings.timezone == "Asia/Seoul"
    assert settings.public_host == "public.test"
    assert settings.admin_host == "admin.test"
    assert settings.booking_horizon_days == 90
    assert type(settings.booking_horizon_days) is int
    assert settings.admin_access == "deny"
    assert create_app(settings=settings).state.settings is settings
    with pytest.raises(FrozenInstanceError):
        settings.admin_access = "allow"


@pytest.mark.parametrize(
    "value",
    [0, -1, "0", "-1", "abc", "", " ", 1.5, 90.0, "1.5", True, False, None],
    ids=lambda value: f"TC-001-02-{type(value).__name__}-{value!r}",
)
def test_tc001_02_invalid_horizon_cannot_create_app(value, db_path):
    with pytest.raises(SettingsError, match="BOOKING_HORIZON_DAYS"):
        create_app(settings=Settings(db_path=db_path, booking_horizon_days=value))
    assert not db_path.parent.exists()


@pytest.mark.parametrize(
    "changes, field",
    [
        ({"public_host": "admin.test", "admin_host": "admin.test"}, "HOST"),
        ({"public_host": "ADMIN.TEST.", "admin_host": "admin.test"}, "HOST"),
        ({"public_host": "public.test/path"}, "PUBLIC_HOST"),
        ({"admin_host": "https://admin.test"}, "ADMIN_HOST"),
        ({"public_host": "public.test:8000"}, "PUBLIC_HOST"),
        ({"public_host": "user@public.test"}, "PUBLIC_HOST"),
        ({"public_host": "public.test?query"}, "PUBLIC_HOST"),
        ({"public_host": "public.test#fragment"}, "PUBLIC_HOST"),
        ({"public_host": ""}, "PUBLIC_HOST"),
        ({"public_host": "bad host.test"}, "PUBLIC_HOST"),
        ({"public_host": "bad..test"}, "PUBLIC_HOST"),
        ({"public_host": "-bad.test"}, "PUBLIC_HOST"),
        ({"public_host": "bad-.test"}, "PUBLIC_HOST"),
        ({"public_host": "bad_host.test"}, "PUBLIC_HOST"),
        ({"public_host": "*.test"}, "PUBLIC_HOST"),
        ({"public_host": "a" * 64 + ".test"}, "PUBLIC_HOST"),
        ({"public_host": ".".join(["a" * 63] * 4)}, "PUBLIC_HOST"),
        ({"public_host": "방.test"}, "PUBLIC_HOST"),
        ({"admin_access": "allow"}, "ADMIN_ACCESS"),
        ({"admin_access": "development"}, "ADMIN_ACCESS"),
        ({"admin_access": "mtls"}, "ADMIN_ACCESS"),
        ({"timezone": "UTC"}, "TIMEZONE"),
        ({"timezone": ""}, "TIMEZONE"),
    ],
    ids=[f"TC-001-03-{i:02d}" for i in range(23)],
)
def test_tc001_03_invalid_hosts_timezone_and_access(changes, field, db_path):
    with pytest.raises(SettingsError, match=field):
        create_app(settings=Settings(db_path=db_path, **changes))
    assert not db_path.parent.exists()


@pytest.mark.parametrize("path", ["", " ", "\0", None])
def test_tc001_03_invalid_database_path(path):
    with pytest.raises(SettingsError, match="DB_PATH"):
        create_app(settings=Settings(db_path=path))


def test_tc001_04_process_environment_over_dotenv_over_defaults(tmp_path, monkeypatch):
    dotenv = tmp_path / ".env"
    file_db = tmp_path / "dotenv.db"
    dotenv.write_text(
        f"ROOMSHARE_DB_PATH={file_db}\n"
        "ROOMSHARE_PUBLIC_HOST=file.test\n"
        "ROOMSHARE_ADMIN_HOST=admin-file.test\n"
        "ROOMSHARE_BOOKING_HORIZON_DAYS=30\n",
        encoding="utf-8",
    )
    defaults = load_settings(env_file=None, environ={})
    assert defaults.booking_horizon_days == 90
    assert defaults.timezone == "Asia/Seoul"
    assert defaults.admin_access == "deny"
    assert defaults.public_host == "room.example.com"
    assert defaults.admin_host == "admin-room.example.com"
    assert defaults.db_path == PROJECT_ROOT / "data" / "roomshare.db"
    from_file = load_settings(env_file=dotenv, environ={})
    assert from_file.booking_horizon_days == 30
    assert from_file.db_path == file_db
    assert from_file.public_host == "file.test"
    assert from_file.admin_host == "admin-file.test"
    assert from_file.timezone == "Asia/Seoul"

    with monkeypatch.context() as patch:
        patch.setenv("ROOMSHARE_BOOKING_HORIZON_DAYS", "45")
        process_before = dict(os.environ)
        from_process = load_settings(env_file=dotenv)
        assert from_process.booking_horizon_days == 45
        assert dict(os.environ) == process_before
        explicit = load_settings(
            env_file=dotenv, environ={"ROOMSHARE_BOOKING_HORIZON_DAYS": "60"}
        )
        assert explicit.booking_horizon_days == 60
        assert explicit.public_host == "file.test"
    assert load_settings(env_file=None).booking_horizon_days == 90
    assert load_settings(env_file=tmp_path / "missing.env", environ={}) == defaults


def test_tc001_04_empty_environment_value_does_not_fall_back(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("ROOMSHARE_BOOKING_HORIZON_DAYS=30\n", encoding="utf-8")
    with pytest.raises(SettingsError, match="BOOKING_HORIZON_DAYS"):
        load_settings(env_file=dotenv, environ={"ROOMSHARE_BOOKING_HORIZON_DAYS": ""})


def test_tc001_04_dotenv_does_not_interpolate_or_modify_environment(tmp_path, monkeypatch):
    dotenv = tmp_path / ".env"
    dotenv.write_text("ROOMSHARE_BOOKING_HORIZON_DAYS=${QA_HORIZON}\n", encoding="utf-8")
    monkeypatch.setenv("QA_HORIZON", "30")
    before = dict(os.environ)
    with pytest.raises(SettingsError, match="BOOKING_HORIZON_DAYS"):
        load_settings(env_file=dotenv, environ={})
    assert dict(os.environ) == before


def test_tc001_04_relative_database_path_is_source_relative(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = Settings(db_path="data/relative-test.db")
    assert settings.db_path == PROJECT_ROOT / "data" / "relative-test.db"
    assert not settings.db_path.exists()


def test_tc001_04_malformed_dotenv_fails_without_echoing_input(tmp_path, caplog):
    dotenv = tmp_path / ".env"
    sentinel = "QA_MALFORMED_VALUE_SENTINEL"
    dotenv.write_text(
        f"ROOMSHARE_BOOKING_HORIZON_DAYS='{sentinel}\n", encoding="utf-8"
    )
    with pytest.raises(SettingsError, match="dotenv") as failure:
        load_settings(env_file=dotenv, environ={})
    assert sentinel not in str(failure.value)
    assert sentinel not in caplog.text


@pytest.mark.parametrize("value", ["0", "-1", "abc", "", "1.5"])
def test_tc001_02_invalid_environment_stops_uvicorn(value, tmp_path, isolated_environment):
    env = dict(os.environ)
    env["ROOMSHARE_BOOKING_HORIZON_DAYS"] = value
    result = subprocess.run(
        [
            sys.executable, "-m", "uvicorn", "app.main:create_app", "--factory",
            "--host", "127.0.0.1", "--port", "0", "--app-dir", str(PROJECT_ROOT),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode != 0
    assert "BOOKING_HORIZON_DAYS" in result.stderr
    assert "Application startup complete" not in result.stderr
    assert not Path(isolated_environment["ROOMSHARE_DB_PATH"]).parent.exists()
