"""Domain failures without HTTP dependencies or submitted input in messages."""

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
import sqlite3


class InvalidInputError(ValueError):
    code = "INVALID_INPUT"

    def __init__(self, field: str):
        self.field = field
        super().__init__(f"Invalid {field}")


class DateConflictError(Exception):
    code = "DATE_CONFLICT"

    def __init__(self, dates: Iterable[str]):
        self.dates = tuple(sorted(set(dates)))
        super().__init__("Requested dates are unavailable")


class TemporaryDatabaseError(Exception):
    code = "DATABASE_BUSY"

    def __init__(self):
        super().__init__("Database is temporarily unavailable")


class InvalidPolicyError(RuntimeError):
    """Missing or invalid persisted policy; never fall back to stale defaults."""


@contextmanager
def database_errors() -> Iterator[None]:
    """Translate only BUSY/LOCKED, including their extended SQLite codes."""
    try:
        yield
    except sqlite3.Error as error:
        code = getattr(error, "sqlite_errorcode", 0)
        if code & 0xff in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            raise TemporaryDatabaseError() from None
        raise
