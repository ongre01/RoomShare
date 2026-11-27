#!/usr/bin/env bash
set -euo pipefail
trap 'echo "Release installation failed at line $LINENO." >&2' ERR
[[ $# == 1 ]] || { echo "Usage: install.sh PRIVATE_CONFIG_DIRECTORY" >&2; exit 2; }
cd -- "$(dirname -- "$0")/.."
config=$1
if [[ $config != /* || ! -d $config || -L $config || ! -O $config || $(stat -c %a -- "$config") != 700 ]]; then
    echo "Expected an absolute, owner-only configuration directory (0700)." >&2
    exit 1
fi
env_file=$config/roomshare.env
if [[ -e $env_file || -L $env_file ]]; then
    if [[ ! -f $env_file || -L $env_file || ! -O $env_file || $(stat -c %a -- "$env_file") != 600 ]]; then
        echo "Existing environment file must be private (0600), regular and owned by installer." >&2
        exit 1
    fi
fi

# Prepare at its final, versioned path: moving a venv breaks its entry points.
umask 022
python3 -m venv .venv
.venv/bin/python -m pip --disable-pip-version-check install --no-input -r requirements.lock
.venv/bin/python -m pip check
if [[ ! -e $env_file ]]; then
    umask 077
    temp=$(mktemp -- "$config/.roomshare-env.XXXXXXXX")
    trap 'rm -f -- "$temp"' EXIT
    cat deploy/roomshare.env.example > "$temp"
    ln -T -- "$temp" "$env_file"
fi
echo "Release prepared; no database, service or proxy was switched."
