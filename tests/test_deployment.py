"""Offline release preparation and maintenance guards; no host services changed."""

from contextlib import closing
import configparser
from datetime import datetime, timezone
import ensurepip
import fcntl
import hashlib
import os
from pathlib import Path
import shlex
import shutil
import socket
from string import Template
import subprocess
import sys
import time

import httpx
import pytest
from app.db import connect
from app.services import admin
from app.services.reservations import create_reservation
from tests.db_helpers import insert_reservation, snapshot


ROOT = Path(__file__).resolve().parents[1]


def run(*args, ok=True, env=None):
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=90, env=env)
    if ok:
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode != 0 and result.stderr
    return result


def test_tc008_01_script_and_unit_syntax():
    for name in ("install", "deploy"):
        run("bash", "-n", ROOT / f"scripts/{name}.sh")
    run("systemd-analyze", "verify", *sorted((ROOT / "deploy/systemd").iterdir()))


@pytest.fixture
def release_checkout(tmp_path):
    checkout = tmp_path / "source"
    (checkout / "app").mkdir(parents=True)
    (checkout / "app/version").write_text("approved\n")
    (checkout / "deploy").mkdir()
    shutil.copy(ROOT / "deploy/roomshare.env.example", checkout / "deploy")
    (checkout / "scripts").mkdir()
    for name in ("deploy.sh", "install.sh", "migrate.py", "snapshot.py", "verify_bootstrap.py"):
        shutil.copy(ROOT / "scripts" / name, checkout / "scripts")
    (checkout / "requirements.lock").write_text(f"pip=={ensurepip.version()}\n")
    (checkout / "requirements-dev.lock").write_text("not for deployment\n")
    git = ("git", "-C", checkout, "-c", "user.name=Release QA", "-c", "user.email=qa@example.invalid",
           "-c", "commit.gpgSign=false")
    run(*git, "init", "-q")
    run(*git, "add", ".")
    run(*git, "commit", "-qm", "Approved release")
    return checkout, git


def test_tc008_02_prepare_uses_approved_commit_and_refuses_overwrite(tmp_path, release_checkout, monkeypatch):
    checkout, git = release_checkout
    rev = run(*git, "rev-parse", "HEAD").stdout.strip()
    lock_digest = hashlib.sha256((checkout / "requirements.lock").read_bytes()).hexdigest()
    (checkout / "app/version").write_text("later commit\n")
    run(*git, "commit", "-qam", "Unapproved release")
    (checkout / "app/version").write_text("working tree\n")
    (checkout / "app/untracked").touch()
    (checkout / ".env").write_text("# local only\n")
    releases = tmp_path / "release space"
    releases.mkdir(mode=0o755)
    config = tmp_path / "config"
    config.mkdir(mode=0o700)
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    monkeypatch.delenv("ROOMSHARE_DB_PATH", raising=False)
    command = (checkout / "scripts/deploy.sh", "prepare", rev, releases, config)

    result = run(*command)
    target = releases / rev
    assert str(target) in result.stdout and lock_digest in result.stdout
    assert (target / "app/version").read_text() == "approved\n"
    assert not any((target / path).exists() for path in
                   ("app/untracked", ".env", ".git", "requirements-dev.lock", "scripts/verify_bootstrap.py"))
    run(target / ".venv/bin/python", "-m", "pip", "check")
    assert target.stat().st_mode & 0o777 == 0o755
    assert (config / "roomshare.env").stat().st_mode & 0o777 == 0o600
    assert not (tmp_path / "current").exists()

    (target / "app/version").write_text("do not overwrite\n")
    run(*command, ok=False)
    assert (target / "app/version").read_text() == "do not overwrite\n"


def test_tc008_02_prepare_failure_keeps_previous_release_and_config(tmp_path, release_checkout, monkeypatch):
    checkout, git = release_checkout
    (checkout / "requirements.lock").write_text("rs008-unavailable-package==1\n")
    run(*git, "commit", "-qam", "Simulate installation failure")
    rev = run(*git, "rev-parse", "HEAD").stdout.strip()
    releases = tmp_path / "releases"
    releases.mkdir(mode=0o755)
    previous = releases / "previous"
    previous.mkdir()
    current = tmp_path / "current"
    current.symlink_to(previous)
    config = tmp_path / "config"
    config.mkdir(mode=0o700)
    settings = config / "roomshare.env"
    settings.write_text("# operator configuration\n")
    settings.chmod(0o600)
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    prepare = (checkout / "scripts/deploy.sh", "prepare")

    run(*prepare, "no-such-revision", releases, config, ok=False)
    assert list(releases.iterdir()) == [previous]
    result = run(*prepare, rev, releases, config, ok=False)
    assert "Release ready:" not in result.stdout
    assert (releases / rev).stat().st_mode & 0o777 == 0o700
    assert current.resolve() == previous and settings.read_text() == "# operator configuration\n"


def test_tc008_02_prepare_refuses_a_dangling_current(tmp_path, release_checkout, monkeypatch):
    checkout, git = release_checkout
    rev = run(*git, "rev-parse", "HEAD").stdout.strip()
    releases = tmp_path / "releases"
    releases.mkdir(mode=0o755)
    config = tmp_path / "config"
    config.mkdir(mode=0o700)
    current = tmp_path / "current"
    current.symlink_to(releases / rev)
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    run(checkout / "scripts/deploy.sh", "prepare", rev, releases, config, ok=False)
    assert current.is_symlink() and not current.exists()
    assert not list(releases.iterdir()) and not list(config.iterdir())


def test_tc008_02_repeat_install_preserves_data_config_and_reports_failure(tmp_path, migrated_db):
    release = tmp_path / "release"
    (release / "scripts").mkdir(parents=True)
    (release / "deploy").mkdir()
    installer = shutil.copy(ROOT / "scripts/install.sh", release / "scripts")
    shutil.copy(ROOT / "deploy/roomshare.env.example", release / "deploy")
    config = tmp_path / "private"
    config.mkdir(mode=0o700)
    # Exercise the real venv and pip without making the regression suite depend
    # on a package index. The full production lock is a separate cold install.
    lock = release / "requirements.lock"
    lock.write_text(f"pip=={ensurepip.version()}\n")
    env = {**os.environ, "PIP_NO_INDEX": "1", "ROOMSHARE_DB_PATH": str(migrated_db)}
    with closing(connect(migrated_db)) as db:
        insert_reservation(db)
        before = snapshot(db)
        run(installer, config, env=env)
        settings = config / "roomshare.env"
        assert settings.stat().st_mode & 0o777 == 0o600
        settings.write_text("ROOMSHARE_ADMIN_ACCESS=deny\n# operator-owned\n")
        run(installer, config, env=env)
        lock.write_text("rs008-missing-package==1\n")
        result = run(installer, config, env=env, ok=False)
        assert "Release prepared" not in result.stdout
        assert settings.read_text().endswith("# operator-owned\n") and snapshot(db) == before


def test_tc008_02_13_init_upgrade_lock_and_failed_migration_keep_original(tmp_path):
    release = tmp_path / "release"
    shutil.copytree(ROOT / "app", release / "app", ignore=shutil.ignore_patterns("__pycache__"))
    scripts = release / "scripts"
    scripts.mkdir()
    for file in ("migrate.py", "deploy.sh", "snapshot.py"):
        shutil.copy(ROOT / "scripts" / file, scripts)
    (release / ".venv").symlink_to(sys.prefix, target_is_directory=True)
    db = tmp_path / "data.db"
    archives = tmp_path / "archives"
    env = {**os.environ, "ROOMSHARE_DB_PATH": str(db), "ROOMSHARE_BACKUP_DIR": str(archives)}
    command = scripts / "deploy.sh"
    run(command, "upgrade", release, env=env, ok=False)
    assert not db.exists()
    run(command, "init", env=env)
    with closing(connect(db)) as connection:
        insert_reservation(connection)
        expected = snapshot(connection)
        run(command, "init", env=env, ok=False)
        with Path(str(db) + ".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_SH)
            run(command, "upgrade", release, env=env, ok=False)
            assert not archives.exists()
        run(command, "upgrade", release, env=env)
        assert snapshot(connection) == expected
        saved, = archives.glob("*.sqlite3")
        old = tmp_path / "previous"
        shutil.copytree(release, old, symlinks=True)
        (release / "app/migrations/002_failure.sql").write_text("DROP TABLE nonexistent;")
        result = run(command, "upgrade", old, env=env, ok=False)
        assert "Database prepared" not in result.stdout and snapshot(connection) == expected
        assert len(list(archives.glob("*.sqlite3"))) == 2
        dest = tmp_path / "rollback"
        run(sys.executable, old / "scripts/snapshot.py", "restore", saved, "--to", dest)
        run(sys.executable, old / "scripts/migrate.py", "--check", "--db-path", dest / "roomshare.db")
        with closing(connect(dest / "roomshare.db")) as restored:
            assert snapshot(restored) == expected


def unit_command(unit, env, directive="ExecStart"):
    config = configparser.ConfigParser(interpolation=None)
    config.read(ROOT / "deploy/systemd" / unit)
    return [
        Template(word).substitute(env).replace("/opt/roomshare/current/.venv/bin/python", sys.executable)
        for word in shlex.split(config["Service"][directive])
    ]


def test_tc008_03_10_14_unit_commands_restart_and_backup_without_host_manager(migrated_db, tmp_path):
    """Supplemental: real processes, not a substitute for a staging systemd manager."""
    def clock():
        return datetime(2026, 11, 30, 15, tzinfo=timezone.utc)

    migrated_db.chmod(0o600)
    create_reservation(migrated_db, name="QA_USER_A", dates=["2026-12-02"], clock=clock)
    admin.block_dates(migrated_db, dates=["2026-12-03"], clock=clock)
    admin.update_settings(migrated_db, booking_horizon_days=45, clock=clock)
    with closing(connect(migrated_db)) as db:
        expected = snapshot(db)
    env = {**os.environ, "ROOMSHARE_DB_PATH": str(migrated_db),
           "ROOMSHARE_BACKUP_DIR": str(tmp_path / "archives"), "ROOMSHARE_BACKUP_RETENTION_DAYS": "1"}
    app = unit_command("roomshare.service", env)
    assert app[app.index("--host") + 1] == "127.0.0.1"
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    app[app.index("--port") + 1] = str(port)
    for _ in range(2):
        run(*unit_command("roomshare.service", env, "ExecStartPre"), env=env)
        with subprocess.Popen(app, env=env, cwd=ROOT, umask=0o077) as server:
            try:
                with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=1) as client:
                    for _ in range(200):
                        assert server.poll() is None, "Isolated service unexpectedly exited"
                        try:
                            health = client.get("/healthz")
                            break
                        except httpx.ConnectError:
                            time.sleep(0.025)
                    else:
                        pytest.fail("Isolated service readiness timeout")
                    assert health.status_code == 200 and health.json() == {"status": "ok"}
                    assert client.get("/api/calendar?month=2026-12").status_code == 200
                with Path(str(migrated_db) + ".lock").open("a") as lock:
                    with pytest.raises(BlockingIOError):
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                run(*unit_command("roomshare-backup.service", env), env=env)
                with closing(connect(migrated_db)) as db:
                    assert snapshot(db) == expected
                assert migrated_db.stat().st_mode & 0o777 == 0o600
            finally:
                server.terminate()
                server.wait(timeout=10)
    archives = list((tmp_path / "archives").glob("*.sqlite3"))
    assert len(archives) == 2
    env["ROOMSHARE_DB_PATH"] = str(tmp_path / "absent")
    run(*unit_command("roomshare-backup.service", env), env=env, ok=False)
    assert not (tmp_path / "absent").exists() and all(path.exists() for path in archives)
