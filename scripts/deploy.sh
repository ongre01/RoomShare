#!/usr/bin/env bash
set -euo pipefail
usage() {
    printf '%s\n' \
        'Usage: deploy.sh prepare APPROVED_REV RELEASES_DIRECTORY PRIVATE_CONFIG_DIRECTORY' \
        '       deploy.sh init' \
        '       deploy.sh upgrade PREVIOUS_RELEASE' \
        'init and upgrade require ROOMSHARE_DB_PATH; upgrade also needs ROOMSHARE_BACKUP_DIR.'
}
if [[ $# == 1 && $1 == --help ]]; then usage; exit 0; fi
cd -- "$(dirname -- "$0")/.."

if [[ ${1:-} == prepare ]]; then
    [[ $# == 4 ]] || { usage >&2; exit 2; }
    trap 'echo "Release preparation failed at line $LINENO; release not switched." >&2' ERR
    if [[ $3 != /* || ! -d $3 || -L ${3%/} || ! -O $3 ]]; then
        echo "Expected an absolute releases directory owned by the installer." >&2
        exit 1
    fi
    releases=$(realpath -e -- "$3")
    mode=$(stat -c %a -- "$releases")
    (( (8#$mode & 022) == 0 )) || { echo "Releases directory must not be writable by group or others." >&2; exit 1; }
    [[ $4 == /* && -d $4 ]] || { echo "Expected an existing absolute private configuration directory." >&2; exit 1; }
    if [[ -L $releases/../current && ! -d $releases/../current ]]; then
        echo "Resolve the broken current symlink before preparing a release." >&2
        exit 1
    fi
    rev=$(git rev-parse --verify --end-of-options "${2}^{commit}")
    release=$releases/$rev

    # Venv entry points embed the final path. Keep it private until installation succeeds.
    mkdir -m 0700 -- "$release"
    umask 022
    git -c tar.umask=0022 archive --format=tar "$rev" -- app requirements.lock deploy \
        scripts/install.sh scripts/deploy.sh scripts/migrate.py scripts/snapshot.py |
        tar -xf - -C "$release" --no-same-owner --no-same-permissions
    "$release/scripts/install.sh" "$4"
    sha256sum -- "$release/requirements.lock"
    chmod 0755 -- "$release"
    printf 'Release ready: %s\nDatabase preparation and service activation are separate steps.\n' "$release"
    exit 0
fi

[[ $# == 1 && $1 == init || $# == 2 && $1 == upgrade ]] || { usage >&2; exit 2; }
trap 'echo "Database preparation failed at line $LINENO; release not switched." >&2' ERR
umask 077
db=${ROOMSHARE_DB_PATH:?Set the absolute database path}
[[ $db == /* && ! -L $db ]] || { echo "Expected an absolute, non-symlink database path." >&2; exit 1; }

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
