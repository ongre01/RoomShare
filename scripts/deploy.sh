#!/usr/bin/env bash
set -euo pipefail
trap 'echo "Database preparation failed at line $LINENO; release not switched." >&2' ERR
umask 077
usage() { echo "Usage: deploy.sh init | upgrade PREVIOUS_RELEASE (explicit ROOMSHARE_DB_PATH required)" >&2; exit 2; }
[[ $# == 1 && $1 == init || $# == 2 && $1 == upgrade ]] || usage
db=${ROOMSHARE_DB_PATH:?Set the absolute database path}
[[ $db == /* && ! -L $db ]] || { echo "Expected an absolute, non-symlink database path." >&2; exit 1; }
cd -- "$(dirname -- "$0")/.."

# The unit holds a shared lock on this same inode for the writer's lifetime.
exec 9>>"$db.lock"
flock -xn 9 || { echo "Database in use; stop the service and backup jobs first." >&2; exit 1; }
if [[ $1 == init ]]; then
    for file in "$db" "$db-wal" "$db-shm" "$db-journal"; do
        [[ ! -e $file && ! -L $file ]] || { echo "Refusing to initialize over existing data." >&2; exit 1; }
    done
else
    [[ $2 == /* && -s $db ]] || { echo "Upgrade needs an existing DB and absolute previous release." >&2; exit 1; }
    # The OLD release validates the snapshot against its own schema.
    "$2/.venv/bin/python" "$2/scripts/snapshot.py" backup "$db" "${ROOMSHARE_BACKUP_DIR:?Set a private backup directory}"
fi
.venv/bin/python scripts/migrate.py --db-path "$db"
.venv/bin/python scripts/migrate.py --db-path "$db" --check
echo "Database prepared and checked; service activation is a separate operator step."
