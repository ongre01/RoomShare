"""Explicit SQLite connections and owned, atomic write transactions."""

from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
import sqlite3


DEFAULT_BUSY_TIMEOUT_MS = 5000


class DatabaseConfigurationError(RuntimeError):
    """A required connection setting could not be enabled."""


def connect(
    db_path: str | Path, *, busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> sqlite3.Connection:
    """Return a caller-owned connection; never create directories or migrate.

    Explicit autocommit leaves transaction boundaries to write_transaction.
    A bare SQLite connection's `with` block does NOT close it: use closing().
    SQLite exceptions and their sqlite_errorcode are preserved for services.
    """
    if type(busy_timeout_ms) is not int or not 0 <= busy_timeout_ms <= 2_147_483_647:
        raise ValueError("busy_timeout_ms must be an integer between 0 and 2147483647")
    connection = sqlite3.connect(
        db_path, timeout=busy_timeout_ms / 1000, isolation_level=None,
    )
    try:
        connection.row_factory = sqlite3.Row
        # sqlite3's seconds float can truncate (e.g. 1001ms -> 1000ms).
        # PRAGMA cannot bind parameters; only the validated integer is formatted.
        connection.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
        connection.execute("PRAGMA foreign_keys = ON")
        journal_mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
        if (
            journal_mode.lower() != "wal"
            or connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1
            or connection.execute("PRAGMA busy_timeout").fetchone()[0] != busy_timeout_ms
        ):
            raise DatabaseConfigurationError("SQLite requires file storage, WAL, FK and busy timeout")
        return connection
    except BaseException:
        connection.close()
        raise


@contextmanager
def write_transaction(
    db_path: str | Path, *, busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> Iterator[sqlite3.Connection]:
    """Own a new connection: BEGIN IMMEDIATE, commit/rollback, always close.

    Callers must use the yielded connection for every write in the operation,
    including logs. Do not commit/rollback/close it or nest another transaction.
    No retry or HTTP error conversion is performed here.
    """
    connection = connect(db_path, busy_timeout_ms=busy_timeout_ms)
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
