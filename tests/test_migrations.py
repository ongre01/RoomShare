"""TC-002-01/02/03/11: real-file migration and command-line contracts."""

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest

from app.db import connect, write_transaction
from app.migrations import MIGRATIONS_DIR, MigrationError, check_database, migrate


ROOT = Path(__file__).resolve().parents[1]
RESERVATION_ID = "00000000-0000-4000-8000-000000000001"


def snapshot(db_path):
    with closing(connect(db_path)) as connection:
        tables = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )]
        return {
            "schema": [tuple(row) for row in connection.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
            )],
            "rows": {table: sorted(tuple(row) for row in connection.execute(
                f'SELECT * FROM "{table}"'
            )) for table in tables},
        }


def seed(db_path):
    with write_transaction(db_path) as connection:
        connection.execute(
            "INSERT INTO reservations (id, name, memo, cancel_token_hash, status) "
            "VALUES (?, ?, ?, ?, ?)",
            (RESERVATION_ID, "QA_USER_A", "QA_MEMO_SENTINEL", "a" * 64, "CONFIRMED"),
        )
        connection.execute(
            "INSERT INTO reserved_dates (date, reservation_id, type) VALUES (?, ?, ?)",
            ("2026-12-02", RESERVATION_ID, "RESERVED"),
        )
        connection.execute(
            "INSERT INTO reservation_logs (reservation_id, action, detail) VALUES (?, ?, ?)",
            (RESERVATION_ID, "CREATED", '{"after":["2026-12-02"]}'),
        )


def test_tc002_01_initial_schema(db_path):
    assert not db_path.exists()
    before = datetime.now(timezone.utc)
    assert migrate(db_path) == (1,)
    after = datetime.now(timezone.utc)
    with closing(connect(db_path)) as connection:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert tables == {
            "reservations", "reserved_dates", "reservation_logs", "settings", "schema_migrations",
        }
        indexes = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )}
        assert {"idx_reserved_dates_reservation_id", "idx_reservation_logs_reservation_id",
                "idx_reservation_logs_created_at"} <= indexes
        for table, key in (("reservations", "id"), ("reserved_dates", "date"), ("settings", "key")):
            columns = {row[1]: row for row in connection.execute(f"PRAGMA table_info({table})")}
            assert columns[key][3] == 1  # TEXT PK must also reject NULL.
            assert columns[key][5] == 1
        assert [row[2] for row in connection.execute("PRAGMA foreign_key_list(reserved_dates)")] == ["reservations"]
        assert [row[2] for row in connection.execute("PRAGMA foreign_key_list(reservation_logs)")] == ["reservations"]
        versions = list(connection.execute("SELECT version, applied_at FROM schema_migrations"))
        assert len(versions) == 1 and versions[0][0] == 1
        applied_at = datetime.fromisoformat(versions[0][1])
        assert applied_at.utcoffset().total_seconds() == 0
        assert before <= applied_at <= after
        assert connection.execute("SELECT value FROM settings WHERE key='booking_horizon_days'").fetchone()[0] == "90"
    assert check_database(db_path) == (1,)


def test_tc002_02_rerun_preserves_all_records(migrated_db):
    seed(migrated_db)
    with write_transaction(migrated_db) as connection:
        connection.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("qa_custom", "preserve"))
    before = snapshot(migrated_db)
    assert migrate(migrated_db) == ()
    assert migrate(migrated_db) == ()
    assert snapshot(migrated_db) == before


@pytest.fixture
def migration_files(tmp_path):
    directory = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS_DIR, directory, ignore=shutil.ignore_patterns("*.py", "__pycache__"))
    return directory


def test_tc002_03_upgrade_failure_rolls_back_all_pending(migrated_db, migration_files):
    seed(migrated_db)
    before = snapshot(migrated_db)
    (migration_files / "002_partial.sql").write_text(
        "CREATE TABLE partial (id INTEGER);\n"
        "INSERT INTO partial VALUES (1);\n"
        "UPDATE reservations SET memo='changed';\n", encoding="utf-8",
    )
    (migration_files / "003_failure.sql").write_text(
        "CREATE INDEX partial_index ON reservations(name);\n"
        "INSERT INTO nonexistent VALUES (1);\n", encoding="utf-8",
    )
    with pytest.raises(sqlite3.OperationalError):
        migrate(migrated_db, migrations_dir=migration_files)
    assert snapshot(migrated_db) == before
    assert check_database(migrated_db) == (1,)


def test_tc002_03_initial_failure_rolls_back_control_table(db_path, migration_files):
    (migration_files / "002_failure.sql").write_text("SELECT * FROM nonexistent;", encoding="utf-8")
    with pytest.raises(sqlite3.OperationalError):
        migrate(db_path, migrations_dir=migration_files)
    with closing(connect(db_path)) as connection:
        assert connection.execute("SELECT name FROM sqlite_master").fetchall() == []
    assert migrate(db_path) == (1,)


@pytest.mark.parametrize("sql", ["COMMIT;", "END;", "ROLLBACK;", "BEGIN;", "SAVEPOINT unsafe;", "PRAGMA foreign_keys=OFF;", "ATTACH ':memory:' AS extra;"])
def test_tc002_03_migration_cannot_break_transaction(migrated_db, migration_files, sql):
    before = snapshot(migrated_db)
    (migration_files / "002_unsafe.sql").write_text(
        "CREATE TABLE partial (id INTEGER);\n" + sql, encoding="utf-8",
    )
    with pytest.raises(sqlite3.DatabaseError):
        migrate(migrated_db, migrations_dir=migration_files)
    assert snapshot(migrated_db) == before


def test_tc002_03_successful_ordered_upgrade(migrated_db, migration_files):
    seed(migrated_db)
    (migration_files / "003_next.sql").write_text(
        "INSERT INTO upgrade_notes VALUES ('third');", encoding="utf-8",
    )
    (migration_files / "002_upgrade.sql").write_text(
        "-- semicolons in strings and trigger bodies must stay intact\n"
        "CREATE TABLE upgrade_notes (note TEXT); INSERT INTO upgrade_notes VALUES ('second;value');\n"
        "CREATE TRIGGER upgrade_note AFTER INSERT ON upgrade_notes WHEN NEW.note='third' BEGIN\n"
        "INSERT INTO upgrade_notes VALUES ('trigger;one'); INSERT INTO upgrade_notes VALUES ('trigger;two'); END;\n"
        "-- trailing comment\n", encoding="utf-8",
    )
    assert migrate(migrated_db, migrations_dir=migration_files) == (2, 3)
    with closing(connect(migrated_db)) as connection:
        assert [row[0] for row in connection.execute("SELECT note FROM upgrade_notes ORDER BY rowid")] == [
            "second;value", "third", "trigger;one", "trigger;two",
        ]
        assert connection.execute("SELECT name FROM reservations").fetchone()[0] == "QA_USER_A"
    assert migrate(migrated_db, migrations_dir=migration_files) == ()
    assert check_database(migrated_db, migrations_dir=migration_files) == (1, 2, 3)


@pytest.mark.parametrize("filename", ["001_duplicate.sql", "003_gap.sql", "bad.sql"])
def test_tc002_03_invalid_migration_sequence_has_no_side_effects(db_path, migration_files, filename):
    (migration_files / filename).write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(MigrationError):
        migrate(db_path, migrations_dir=migration_files)
    assert not db_path.parent.exists()


def test_tc002_03_unknown_history_is_refused_without_downgrade(migrated_db):
    with write_transaction(migrated_db) as connection:
        connection.execute("INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)", (2, "2026-12-01T00:00:00+00:00"))
    before = snapshot(migrated_db)
    with pytest.raises(MigrationError):
        migrate(migrated_db)
    assert snapshot(migrated_db) == before


def test_tc002_11_seed_uses_initial_settings_and_preserves_changes(db_path):
    assert migrate(db_path, booking_horizon_days=45) == (1,)
    with write_transaction(db_path) as connection:
        assert connection.execute("SELECT value FROM settings WHERE key='booking_horizon_days'").fetchone()[0] == "45"
        connection.execute("UPDATE settings SET value=? WHERE key=?", ("30", "booking_horizon_days"))
    assert migrate(db_path, booking_horizon_days=120) == ()
    with closing(connect(db_path)) as connection:
        assert connection.execute("SELECT value FROM settings WHERE key='booking_horizon_days'").fetchone()[0] == "30"


@pytest.mark.parametrize("horizon", [0, -1, True, 1.5, "90", None])
def test_tc002_11_invalid_seed_does_not_create_database(db_path, horizon):
    with pytest.raises(ValueError):
        migrate(db_path, booking_horizon_days=horizon)
    assert not db_path.parent.exists()


def test_tc002_01_cli_migrate_and_check_from_external_cwd(db_path, tmp_path):
    args = [sys.executable, str(ROOT / "scripts/migrate.py"), "--db-path", str(db_path)]
    missing = subprocess.run(args + ["--check"], cwd=tmp_path, capture_output=True, text=True, timeout=15)
    assert missing.returncode == 1
    assert not db_path.parent.exists()
    first = subprocess.run(args, cwd=tmp_path, capture_output=True, text=True, timeout=15)
    assert first.returncode == 0, first.stderr
    assert "applied: 1" in first.stdout
    before = snapshot(db_path)
    second = subprocess.run(args, cwd=tmp_path, capture_output=True, text=True, timeout=15)
    assert second.returncode == 0 and "up to date" in second.stdout
    check = subprocess.run(args + ["--check"], cwd=tmp_path, capture_output=True, text=True, timeout=15)
    assert check.returncode == 0 and "integrity: ok" in check.stdout
    assert snapshot(db_path) == before
    assert str(db_path) not in first.stdout + first.stderr + check.stdout + missing.stderr


def test_tc002_10_check_rejects_unmigrated_file(db_path):
    db_path.parent.mkdir(parents=True)
    with closing(sqlite3.connect(db_path)):
        pass
    with pytest.raises(MigrationError):
        check_database(db_path)
    with closing(sqlite3.connect(db_path)) as connection:
        assert connection.execute("SELECT name FROM sqlite_master").fetchall() == []
