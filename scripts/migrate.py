"""Explicit migration CLI; --check only reads an existing database."""

import argparse
from dataclasses import replace
from pathlib import Path
import sqlite3
import sys


# Like the bootstrap verifier, allow invocation from any working directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import SettingsError, load_settings
from app.db import DatabaseConfigurationError
from app.migrations import MigrationError, check_database, migrate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, help="Override the configured SQLite file path")
    parser.add_argument("--check", action="store_true", help="Check existing versions and integrity only")
    args = parser.parse_args(argv)
    try:
        settings = load_settings()
        if args.db_path is not None:
            settings = replace(settings, db_path=args.db_path)
        if args.check:
            versions = check_database(settings.db_path)
            print("schema: " + ", ".join(map(str, versions)) + "; integrity: ok; foreign keys: ok")
        else:
            versions = migrate(settings.db_path, booking_horizon_days=settings.booking_horizon_days)
            print("applied: " + ", ".join(map(str, versions)) if versions else "up to date")
    except (SettingsError, MigrationError, DatabaseConfigurationError, sqlite3.Error, OSError, UnicodeError):
        # Do not echo SQL, configuration, paths or database content into logs.
        print("Database migration/check failed; verify configuration, versions and database access.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
