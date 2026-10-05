#!/bin/sh
set -e

ENV_FILE="${ENV_FILE:-/app-env/.env}"
CONFIG_DIR="$(dirname "${CONFIG_FILE:-/config/services.json}")"

# Older releases ran as root, so existing bind mounts may hold root-owned files the
# unprivileged user cannot write. Probe by actually writing (permission bits lie on
# some setups, e.g. Docker Desktop): append-open files without changing them, and
# create+delete a temp file in each directory. Prints the first unwritable path.
first_unwritable() {
    find "$@" 2>/dev/null | while IFS= read -r path; do
        if [ -d "$path" ]; then
            probe="$path/.takeout-write-test"
            { true >"$probe" && rm -f "$probe"; } 2>/dev/null || { echo "$path"; break; }
        else
            { true >>"$path"; } 2>/dev/null || { echo "$path"; break; }
        fi
    done
}

bad="$(first_unwritable "$ENV_FILE" "$CONFIG_DIR" \
    "${BACKUP_ROOT:-/backups}" "${LOG_ROOT:-/logs}" "${STATE_ROOT:-/state}")"
if [ -n "$bad" ]; then
    ids="$(id -u):$(id -g)"
    echo "ERROR: $bad is not writable by uid $ids (files left by an older root container?)." >&2
    echo "  In the compose folder run: sudo chown -R $ids backups logs state config .env" >&2
    echo "  or set PUID/PGID in .env to the uid/gid that owns those files." >&2
    exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
    mkdir -p "$(dirname "$ENV_FILE")"
    touch "$ENV_FILE"
    chmod 600 "$ENV_FILE"
fi

exec "$@"
