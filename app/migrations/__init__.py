"""Ordered SQL migrations, explicitly invoked; importing performs no I/O."""

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3

from app.db import DEFAULT_BUSY_TIMEOUT_MS, write_transaction


MIGRATIONS_DIR = Path(__file__).resolve().parent
_VERSION_FILE = re.compile(r"([0-9]{3})_[a-z0-9_]+\.sql")
_CONTROL_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER NOT NULL PRIMARY KEY CHECK (version > 0),
    applied_at TEXT NOT NULL
)
"""


class MigrationError(RuntimeError):
    """Invalid version history, migration layout, or database integrity."""


def _load_migrations(directory: str | Path) -> list[tuple[int, str]]:
    migrations = []
    for path in sorted(Path(directory).glob("*.sql")):
        match = _VERSION_FILE.fullmatch(path.name)
        if match is None:
            raise MigrationError("Invalid SQL migration filename")
        migrations.append((int(match[1]), path.read_text(encoding="utf-8")))
    if not migrations or [version for version, _ in migrations] != list(range(1, len(migrations) + 1)):
        raise MigrationError("Migration versions must be unique and consecutive from 1")
    return migrations


def _history(connection: sqlite3.Connection, migrations: list[tuple[int, str]]) -> tuple[int, ...]:
    applied = tuple(row[0] for row in connection.execute(
        "SELECT version FROM schema_migrations ORDER BY version"
    ))
    available = tuple(version for version, _ in migrations)
    if applied != available[:len(applied)]:
        raise MigrationError("Unsupported migration history; downgrade is not allowed")
    return applied


def _migration_authorizer(action, arg1, arg2, database, trigger):
    # SQL files cannot escape the runner's transaction or change connection rules.
    if action in {
        sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_SAVEPOINT,
        sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH, sqlite3.SQLITE_PRAGMA,
    }:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _execute_sql(connection: sqlite3.Connection, script: str) -> None:
    # executescript() implicitly commits pending work. execute() plus SQLite's
    # parser preserves quoted semicolons/comments/trigger bodies and atomic DDL.
    connection.set_authorizer(_migration_authorizer)
    try:
        pending = ""
        for character in script:
            pending += character
            if character == ";" and sqlite3.complete_statement(pending):
                connection.execute(pending)
                pending = ""
        if pending.strip():
            connection.execute(pending)
    finally:
        connection.set_authorizer(None)


def migrate(
    db_path: str | Path,
    *,
    booking_horizon_days: int = 90,
    migrations_dir: str | Path = MIGRATIONS_DIR,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> tuple[int, ...]:
    """Create parents explicitly, then apply ALL pending versions atomically.

    Seed the initial Settings value only when applying version 1. Existing DB
    settings win on subsequent runs. Return only the versions applied this run.
    A failed first run can leave an empty file/directory, never a partial schema.
    """
    if type(booking_horizon_days) is not int or booking_horizon_days <= 0:
        raise ValueError("booking_horizon_days must be a positive integer")
    migrations = _load_migrations(migrations_dir)
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with write_transaction(path, busy_timeout_ms=busy_timeout_ms) as connection:
        connection.execute(_CONTROL_TABLE)
        applied = _history(connection, migrations)
        pending = migrations[len(applied):]
        for version, script in pending:
            _execute_sql(connection, script)
            if version == 1:
                connection.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING",
                    ("booking_horizon_days", str(booking_horizon_days)),
                )
            connection.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (version, datetime.now(timezone.utc).isoformat()),
            )
        _check_integrity(connection)
    return tuple(version for version, _ in pending)


def _check_integrity(connection: sqlite3.Connection) -> None:
    if [row[0] for row in connection.execute("PRAGMA integrity_check")] != ["ok"]:
        raise MigrationError("SQLite integrity check failed")
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise MigrationError("SQLite foreign key check failed")


def check_database(
    db_path: str | Path, *, migrations_dir: str | Path = MIGRATIONS_DIR,
) -> tuple[int, ...]:
    """Check an existing DB in read-only mode; never create, repair or migrate."""
    migrations = _load_migrations(migrations_dir)
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, isolation_level=None)) as connection:
        connection.execute("BEGIN")  # one consistent snapshot for all checks
        if not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone():
            raise MigrationError("Database has not been migrated")
        applied = _history(connection, migrations)
        if applied != tuple(version for version, _ in migrations):
            raise MigrationError("Database has pending migrations")
        _check_integrity(connection)
        if connection.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
            raise MigrationError("Database must use WAL")
        return applied
