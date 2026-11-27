"""Online RoomShare backup and isolated restore; never replace a database."""

import argparse
from contextlib import closing
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.migrations import MigrationError, check_database


STAMP = "%Y%m%dT%H%M%S%fZ"
ARCHIVE = re.compile(r"roomshare-([0-9]{8}T[0-9]{12}Z)\.sqlite3")


class SnapshotError(RuntimeError):
    """Operator-facing error; messages contain no database content or secrets."""


def private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise SnapshotError("Backup directory must be owned by this account with mode 0700.")


def publish(staged: Path, target: Path) -> None:
    with staged.open("rb") as handle:
        os.fsync(handle.fileno())
    os.link(staged, target)  # Same filesystem, atomic visibility, never clobber.
    directory = os.open(target.parent, os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def verified_copy(archive: Path, staged: Path) -> None:
    if (archive.is_symlink() or not archive.is_file()
            or any(os.path.lexists(str(archive) + ext) for ext in ("-wal", "-shm", "-journal"))):
        raise SnapshotError("Restore requires a sealed, standalone snapshot, not a live database.")
    shutil.copyfile(archive, staged)
    staged.chmod(0o600)
    check_database(staged)


def prune(directory: Path, latest: Path, days: int, scratch: Path) -> None:
    cutoff = int(time.time()) - days * 86400
    for path in directory.iterdir():
        match = ARCHIVE.fullmatch(path.name)
        if not match or path == latest or not stat.S_ISREG(path.lstat().st_mode):
            continue
        created = datetime.strptime(match[1], STAMP).replace(tzinfo=timezone.utc)
        if created.timestamp() < cutoff:
            # Validate private copies, so read-only WAL checks never leave
            # sidecars beside an archive or mix journals from two snapshots.
            with tempfile.TemporaryDirectory(dir=scratch) as check_dir:
                verified_copy(path, Path(check_dir) / "check.db")
            path.unlink()


def backup(source: Path, directory: Path, days: int) -> Path:
    if days < 0:
        raise SnapshotError("Retention days must be nonnegative; zero disables deletion.")
    if source.is_symlink() or not source.is_file() or directory.resolve() in source.resolve().parents:
        raise SnapshotError("Backup requires an existing database outside the archive directory.")
    private_directory(directory)
    deadline = time.monotonic() + 300

    def bounded(status: int, remaining: int, total: int) -> None:
        if time.monotonic() >= deadline:
            raise SnapshotError("Online backup deadline exceeded.")

    with tempfile.TemporaryDirectory(prefix=".snapshot-", dir=directory) as work:
        staged = Path(work) / "snapshot.db"
        staged.touch(mode=0o600)
        with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as reader:
            with closing(sqlite3.connect(staged)) as writer:
                reader.backup(writer, pages=128, progress=bounded, sleep=0.1)
        check_database(staged)
        stamp = datetime.now(timezone.utc).strftime(STAMP)
        latest = directory / f"roomshare-{stamp}.sqlite3"
        publish(staged, latest)
        if days:
            prune(directory, latest, days, Path(work))
    return latest


def restore(archive: Path, destination: Path | None) -> Path:
    if destination is not None and os.path.lexists(destination):
        raise SnapshotError("Restore directory already exists; refusing to replace existing data.")
    parent = destination.parent if destination is not None else None
    with tempfile.TemporaryDirectory(prefix=".restore-", dir=parent) as work:
        staged = Path(work) / "verified.db"
        verified_copy(archive, staged)
        if destination is None:
            destination = Path(tempfile.mkdtemp(prefix="roomshare-restore-", dir=Path(work).parent))
        else:
            destination.mkdir(mode=0o700)
        target = destination / "roomshare.db"
        publish(staged, target)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    save = commands.add_parser("backup")
    save.add_argument("database", type=Path)
    save.add_argument("directory", type=Path)
    save.add_argument("--retention-days", type=int, default=0)
    recover = commands.add_parser("restore")
    recover.add_argument("archive", type=Path)
    recover.add_argument("--to", type=Path, help="NEW directory; default is a private temporary directory")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.action == "backup":
            result = backup(args.database, args.directory, args.retention_days)
        else:
            result = restore(args.archive, args.to)
    except (SnapshotError, MigrationError) as exc:
        print(f"{args.action} failed: {exc}", file=sys.stderr)
        return 1
    except (sqlite3.Error, OSError, ValueError):
        print(f"{args.action} failed: check database integrity, archive names, access and space.", file=sys.stderr)
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
