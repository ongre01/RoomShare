"""RS-008 recovery checks on migrated, private SQLite files only."""

from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest

from app.db import connect
from app.services import admin
from app.services.reservations import create_reservation
from tests.db_helpers import assert_db_invariants, snapshot


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 30, 15, tzinfo=timezone.utc)


def cli(*args, ok=True):
    result = subprocess.run([sys.executable, ROOT / "scripts/snapshot.py", *map(str, args)],
                            capture_output=True, text=True, timeout=15)
    if ok:
        assert result.returncode == 0, result.stderr
        return Path(result.stdout.strip())
    assert result.returncode != 0 and "failed" in result.stderr and not result.stdout


def test_tc008_11_wal_online_backup_and_isolated_restore(migrated_db, tmp_path, monkeypatch):
    archives = tmp_path / "archives"
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    with closing(connect(migrated_db)) as writer:
        create_reservation(migrated_db, name="QA_USER_A", dates=["2026-12-02"], clock=lambda: NOW)
        admin.block_dates(migrated_db, dates=["2026-12-03"], clock=lambda: NOW)
        admin.update_settings(migrated_db, booking_horizon_days=45, clock=lambda: NOW)
        expected = snapshot(writer)
        assert Path(str(migrated_db) + "-wal").stat().st_size > 0
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("DELETE FROM reserved_dates")
        archive = cli("backup", migrated_db, archives)
        writer.execute("ROLLBACK")
        assert snapshot(writer) == expected
    assert list(archives.iterdir()) == [archive]
    restored = cli("restore", archive)
    assert restored.is_relative_to(tmp_path) and restored.parent != migrated_db.parent
    assert archives.stat().st_mode & 0o777 == restored.parent.stat().st_mode & 0o777 == 0o700
    assert archive.stat().st_mode & 0o777 == restored.stat().st_mode & 0o777 == 0o600
    with closing(connect(restored)) as db:
        assert snapshot(db) == expected
        assert_db_invariants(db)


@pytest.mark.parametrize("damage", ["truncated", "foreign-key", "future-schema", "sidecar"])
def test_tc008_12_rejects_damaged_archive_without_publishing(migrated_db, tmp_path, damage):
    archive = cli("backup", migrated_db, tmp_path / "archives")
    if damage == "truncated":
        archive.write_bytes(archive.read_bytes()[:100])
    elif damage == "sidecar":
        Path(str(archive) + "-wal").touch()
    else:
        with closing(sqlite3.connect(archive)) as db:
            if damage == "foreign-key":
                db.execute("INSERT INTO reserved_dates(date,type,reservation_id) VALUES (?,?,?)",
                           ("2026-12-03", "RESERVED", "nonexistent"))
            else:
                db.execute("UPDATE schema_migrations SET version=2")
            db.commit()
    dest = tmp_path / "recovered"
    cli("restore", archive, "--to", dest, ok=False)
    assert not dest.exists()
    with closing(connect(migrated_db)) as db:
        assert_db_invariants(db)


def test_tc008_13_restore_cannot_replace_live_or_preexisting_directory(migrated_db, tmp_path):
    with closing(connect(migrated_db)) as live:
        archive = cli("backup", migrated_db, tmp_path / "archives")
        expected = snapshot(live)
        admin.update_settings(migrated_db, booking_horizon_days=45, clock=lambda: NOW)
        current = snapshot(live)
        assert current != expected
        cli("restore", archive, "--to", migrated_db.parent, ok=False)
        assert snapshot(live) == current
        dest = cli("restore", archive, "--to", tmp_path / "approved-rehearsal")
        # Validate with the compatible release; do not switch a running service.
        subprocess.run([sys.executable, ROOT / "scripts/migrate.py", "--db-path", dest, "--check"],
                       check=True, capture_output=True, timeout=10)
        with closing(connect(dest)) as restored:
            assert snapshot(restored) == expected
        assert snapshot(live) == current


def test_tc008_14_expiry_only_after_valid_backup_and_only_verified_archives(migrated_db, tmp_path):
    directory = tmp_path / "archives"
    first = cli("backup", migrated_db, directory)
    now = datetime.now(timezone.utc)

    def name(age):
        return directory / (now - age).strftime("roomshare-%Y%m%dT%H%M%S%fZ.sqlite3")

    old = name(timedelta(days=2, minutes=1))
    young = name(timedelta(days=2, minutes=-1))
    shutil.copyfile(first, old)
    shutil.copyfile(first, young)
    unrelated = directory / "operator-copy.sqlite3"
    unrelated.touch()
    cli("backup", migrated_db, directory)
    assert old.exists()  # No deletion policy by default.
    cli("backup", tmp_path / "absent.db", directory, "--retention-days", 2, ok=False)
    assert not (tmp_path / "absent.db").exists() and old.exists()
    latest = cli("backup", migrated_db, directory, "--retention-days", 2)
    assert latest.is_file() and first.is_file() and young.is_file() and unrelated.exists()
    assert not old.exists()
    old.write_bytes(b"corrupt")
    cli("backup", migrated_db, directory, "--retention-days", 2, ok=False)
    assert old.read_bytes() == b"corrupt" and latest.is_file()
    assert not list(directory.glob(".snapshot-*"))


def test_tc008_11_failed_backup_never_publishes_partial_file(migrated_db, tmp_path):
    archives = tmp_path / "archives"
    previous = cli("backup", migrated_db, archives)
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"not a database")
    cli("backup", bad, archives, ok=False)
    assert list(archives.iterdir()) == [previous]
    archives.chmod(0o755)
    cli("backup", migrated_db, archives, ok=False)
    assert list(archives.iterdir()) == [previous]
