"""Opt-in systemd rehearsal. Only transient --user units, loopback and disposable data."""

import configparser
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import socket
import stat
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.db import connect
from app.migrations import migrate
from app.services.admin import block_dates, update_settings
from app.services.reservations import create_reservation
from scripts.snapshot import STAMP, backup, restore
from tests.db_helpers import assert_db_invariants, snapshot
from tests.deployment.test_proxy import ADMIN, PUBLIC, local_port, pki, tls_proxy
from tests.test_deployment import ROOT, run, unit_command


def property_of(unit, key):
    return run("systemctl", "--user", "show", unit, "-p", key, "--value").stdout.strip()


def wait_for(check, description):
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.2)
    raise AssertionError(f"Timed out waiting for {description}")


def logical_state(db):
    with closing(connect(db)) as conn:
        assert_db_invariants(conn)
        return snapshot(conn)


@contextmanager
def transient(name, work, template, command, *, timer=False):
    config = configparser.ConfigParser(interpolation=None)
    config.read(ROOT / "deploy/systemd" / template)
    service = config["Service"]
    paths = {"/var/lib/roomshare": work / "data", "/var/backups/roomshare": work / "archives"}
    writable = shlex.join([str(paths[p]) for p in service["ReadWritePaths"].split()])
    options = [f"--property={key}={service[key]}" for key in (
        "Type", "UMask", "ProtectSystem", "ProtectHome", "PrivateTmp", "PrivateDevices",
        "NoNewPrivileges", "StandardOutput", "StandardError", "Environment",
    )]
    options += ["-p", f"BindReadOnlyPaths={shlex.join([str(work), sys.prefix])}",
                "-p", f"ReadWritePaths={writable}", "-p", f"EnvironmentFile={work}/qa.env",
                "-p", f"WorkingDirectory={work}/release"]
    if timer:
        tick = datetime.now(timezone.utc) + timedelta(seconds=5)
        # Keep a failed oneshot loaded until its Result and ExecMainStatus are read.
        options += ["--remain-after-exit", "--on-calendar", tick.strftime("%Y-%m-%d %H:%M:%S UTC"),
                    "--timer-property=AccuracySec=100ms"]
    else:
        options += ["--collect", "--property=RuntimeMaxSec=180s",
                    "-p", "ExecStartPre=" + shlex.join(unit_command(template, {}, "ExecStartPre"))]
    units = ([name + ".timer"] if timer else []) + [name + ".service"]
    run("systemd-run", "--user", "--no-ask-password", "--quiet", "--unit", name,
        *options, "--", *command)
    try:
        yield
    finally:
        for unit in units:
            if property_of(unit, "ActiveState") == "failed":
                run("systemctl", "--user", "reset-failed", unit)
            stopped = subprocess.run(["systemctl", "--user", "stop", unit], capture_output=True, text=True, timeout=15)
            # Releasing the last reference may already have collected the unit.
            if stopped.returncode != 0:
                assert stopped.returncode == 5 and property_of(unit, "LoadState") == "not-found", stopped.stderr
        for unit in units:
            wait_for(lambda: property_of(unit, "LoadState") == "not-found", f"collection of {unit}")


def test_tc008_03_10_14_real_user_manager(pki):
    assert os.geteuid() != 0 and sys.prefix != sys.base_prefix, "Run as a non-root user in a venv"
    name = "roomshare-qa-" + uuid4().hex
    with TemporaryDirectory(prefix="roomshare-unit-") as temp:
        work = Path(temp)
        release, data, archives = work / "release", work / "data", work / "archives"
        for directory in (release, data, archives):
            directory.mkdir(mode=0o700)
        shutil.copytree(ROOT / "app", release / "app", ignore=shutil.ignore_patterns("__pycache__"))
        (release / "scripts").mkdir()
        for script in ("migrate.py", "snapshot.py"):
            shutil.copy(ROOT / "scripts" / script, release / "scripts")
        db = data / "test.db"
        db.touch(mode=0o600)
        migrate(db)
        first = (datetime.now(ZoneInfo("Asia/Seoul")).date().replace(day=28) + timedelta(days=4)).replace(day=1)
        booked, blocked = first.isoformat(), (first + timedelta(days=1)).isoformat()
        create_reservation(db, name="QA_USER_A", dates=[booked])
        block_dates(db, dates=[blocked])
        update_settings(db, booking_horizon_days=45)
        expected = logical_state(db)

        secret = secrets.token_urlsafe(32)
        env = {"ROOMSHARE_DB_PATH": str(db), "ROOMSHARE_BACKUP_DIR": str(archives),
               "ROOMSHARE_BACKUP_RETENTION_DAYS": "2", "ROOMSHARE_ADMIN_ACCESS": "proxy_mtls",
               "ROOMSHARE_PUBLIC_HOST": PUBLIC, "ROOMSHARE_ADMIN_HOST": ADMIN,
               "ROOMSHARE_ADMIN_PROXY_SECRET": secret}
        env_file = work / "qa.env"
        env_file.touch(mode=0o600)
        env_file.write_text("".join(f"{k}={v}\n" for k, v in env.items()))
        command = unit_command("roomshare.service", env)
        assert command[command.index("--host") + 1] == "127.0.0.1"
        port = local_port()
        command[command.index("--port") + 1] = str(port)

        def listening():
            with socket.socket() as sock:
                return sock.connect_ex(("127.0.0.1", port)) == 0

        with transient(name, work, "roomshare.service", command):
            wait_for(listening, "initial listener")
            with tls_proxy(port, pki, secret) as (request, _):
                pids = []
                for attempt in range(2):
                    if attempt:
                        run("systemctl", "--user", "restart", name + ".service")
                        wait_for(listening, "restarted listener")
                    pid = int(property_of(name, "MainPID"))
                    pids.append(pid)
                    proc = Path("/proc") / str(pid)
                    assert proc.stat().st_uid == os.geteuid() != 0
                    listeners = run("ss", "-Hltnp", f"sport = :{port}").stdout.splitlines()
                    assert len(listeners) == 1 and f"127.0.0.1:{port}" in listeners[0] and f"pid={pid}," in listeners[0]
                    mounts = {row.split()[4]: row.split()[5].split(",")
                              for row in (proc / "mountinfo").read_text().splitlines()}
                    for path in (release, env_file, archives, data):
                        nearest = next(str(p) for p in (path, *path.parents) if str(p) in mounts)
                        assert ("rw" if path == data else "ro") in mounts[nearest]
                    assert stat.S_IMODE(db.stat().st_mode) == 0o600 and stat.S_IMODE(data.stat().st_mode) == 0o700
                    status, _, body = request(PUBLIC, "/healthz")
                    assert status == 200 and json.loads(body) == {"status": "ok"}
                    status, _, body = request(PUBLIC, f"/api/calendar?month={booked[:7]}")
                    states = {d["date"]: d["status"] for d in json.loads(body)["dates"]}
                    assert status == 200 and (states[booked], states[blocked]) == ("RESERVED", "BLOCKED")
                    status, _, body = request(ADMIN, "/api/admin/settings", cert="valid")
                    assert status == 200 and json.loads(body) == {"booking_horizon_days": 45}
                    assert bool(logical_state(db) == expected)
                assert pids[0] != pids[1]
                print(f"user unit restart: MainPID {pids[0]} -> {pids[1]}; HTTPS/mTLS, DB and modes matched")

            initial = backup(db, archives, days=0)
            cutoff = datetime.now(timezone.utc) - timedelta(days=2)
            old, young = [archives / ("roomshare-" + (cutoff + timedelta(minutes=m)).strftime(STAMP) + ".sqlite3")
                          for m in (-1, 1)]
            for path in (old, young):
                shutil.copy2(initial, path)
            job = name + "-backup"
            template = "roomshare-backup.service"
            with transient(job, work, template, unit_command(template, env), timer=True):
                assert job + ".timer" in run("systemctl", "--user", "list-timers", "--all", job + ".timer").stdout
                wait_for(lambda: property_of(job, "SubState") == "exited", "scheduled backup")
                fired = property_of(job + ".timer", "LastTriggerUSec")
                assert fired and property_of(job, "Result") == "success" and property_of(job, "ExecMainStatus") == "0"
                assert not old.exists() and initial.exists() and young.exists()
                latest, = set(archives.glob("*.sqlite3")) - {initial, young}
                old.write_bytes(b"invalid QA snapshot")
                run("systemctl", "--user", "restart", job + ".service", ok=False)
                assert property_of(job, "Result") == "exit-code" and property_of(job, "ExecMainStatus") == "1"
                log = run("journalctl", "--user", "-u", job, "-g", "backup failed:", "-q", "-o", "cat").stdout
                assert "backup failed:" in log and old.read_bytes() == b"invalid QA snapshot"
                restored = restore(latest, work / "recovered")
                assert stat.S_IMODE(latest.stat().st_mode) == stat.S_IMODE(restored.stat().st_mode) == 0o600
                assert bool(logical_state(restored) == logical_state(db) == expected)
                print(f"user timer fired: {fired}; backup success/0, injected failure exit-code/1 in journal; restored")
