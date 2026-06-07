#!/bin/sh
set -e

ENV_FILE="${ENV_FILE:-/app-env/.env}"

if [ ! -f "$ENV_FILE" ]; then
    mkdir -p "$(dirname "$ENV_FILE")"
    touch "$ENV_FILE"
    chmod 600 "$ENV_FILE"
fi

exec "$@"
