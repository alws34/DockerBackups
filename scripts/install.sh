#!/bin/sh
# Install Homelab Takeout as a native systemd service. Run from a cloned repo:
#   sudo ./scripts/install.sh [--with-bitwarden]   install (safe to re-run)
#   sudo ./scripts/install.sh --upgrade            re-copy code, reinstall deps, restart
#   sudo ./scripts/install.sh --uninstall [--purge]
#
# Testing aid: HT_NO_SYSTEMCTL=1 skips every systemctl call (for containers
# without a running systemd). PYTHON=/path/to/python3.x picks the interpreter.
set -eu

NAME=homelab-takeout
PREFIX=/opt/$NAME
CONF_DIR=/etc/$NAME
DATA_DIR=/var/lib/$NAME
LOG_DIR=/var/log/$NAME
UNIT=/etc/systemd/system/$NAME.service
PYTHON="${PYTHON:-python3}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"

usage() {
    sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
}

die() {
    echo "error: $*" >&2
    exit 1
}

systemctl_() {
    if [ "${HT_NO_SYSTEMCTL:-}" = 1 ]; then
        echo "(skipped) systemctl $*"
    else
        systemctl "$@"
    fi
}

mode=install
with_bw=0
purge=0
for arg in "$@"; do
    case "$arg" in
        --upgrade) mode=upgrade ;;
        --uninstall) mode=uninstall ;;
        --purge) purge=1 ;;
        --with-bitwarden) with_bw=1 ;;
        -h | --help) usage; exit 0 ;;
        *) usage >&2; die "unknown option: $arg" ;;
    esac
done
[ "$purge" = 0 ] || [ "$mode" = uninstall ] || die "--purge only works with --uninstall"

[ "$(uname -s)" = Linux ] || die "Linux only"
[ "$(id -u)" = 0 ] || die "run as root: sudo $0 $*"
if [ "${HT_NO_SYSTEMCTL:-}" != 1 ]; then
    [ -d /run/systemd/system ] || die "systemd is not running on this machine"
fi
# sudo may carry a restrictive umask; code under $PREFIX must be world-readable.
umask 022

uninstall() {
    if [ -f "$UNIT" ]; then
        systemctl_ disable --now "$NAME" || true
        rm -f "$UNIT"
        systemctl_ daemon-reload
    fi
    rm -rf "$PREFIX"
    if [ "$purge" = 1 ]; then
        rm -rf "$CONF_DIR" "$DATA_DIR" "$LOG_DIR"
        if id "$NAME" >/dev/null 2>&1; then userdel "$NAME"; fi
        echo "Removed $NAME, including config, backups and logs."
    else
        echo "Removed $NAME. Kept config ($CONF_DIR), data ($DATA_DIR) and logs ($LOG_DIR)."
        echo "Delete them too with: sudo $0 --uninstall --purge"
    fi
}

if [ "$mode" = uninstall ]; then
    uninstall
    exit 0
fi

if [ "$mode" = upgrade ]; then
    [ -f "$UNIT" ] || die "$NAME is not installed; run without --upgrade first"
    # Keep the Bitwarden CLI if it was installed before.
    if [ -x "$PREFIX/bw/bin/bw" ]; then with_bw=1; fi
fi

# ── Requirements ─────────────────────────────────────────────────────────────
command -v "$PYTHON" >/dev/null 2>&1 || die "$PYTHON not found; install Python 3.12 or newer"
"$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 12))' ||
    die "Python 3.12 or newer is required, found $("$PYTHON" -V 2>&1). Set PYTHON=/path/to/python3.12 to pick another."
"$PYTHON" -c 'import ensurepip, venv' 2>/dev/null ||
    die "Python venv support is missing (Debian/Ubuntu: apt install python3-venv)"

# ── Service user and directories ────────────────────────────────────────────
if ! id "$NAME" >/dev/null 2>&1; then
    useradd --system --user-group --no-create-home --home-dir "$DATA_DIR" \
        --shell "$(command -v nologin || echo /bin/false)" "$NAME"
fi
install -d -m 755 "$PREFIX"
install -d -m 700 -o "$NAME" -g "$NAME" "$CONF_DIR" "$DATA_DIR" "$DATA_DIR/backups" "$DATA_DIR/state" "$LOG_DIR"

# Config: copy defaults only when absent, never overwrite the user's edits.
[ -f "$CONF_DIR/services.json" ] || cp "$REPO/config/services.json" "$CONF_DIR/services.json"
[ -f "$CONF_DIR/.env" ] || cp "$REPO/.env.example" "$CONF_DIR/.env"
chown "$NAME:$NAME" "$CONF_DIR/services.json" "$CONF_DIR/.env"
chmod 600 "$CONF_DIR/services.json" "$CONF_DIR/.env"

# ── Code and Python dependencies ────────────────────────────────────────────
rm -rf "$PREFIX/app" "$PREFIX/venv"
cp -R "$REPO/app" "$PREFIX/app"
find "$PREFIX/app" -name __pycache__ -prune -exec rm -rf {} +
chmod -R u=rwX,go=rX "$PREFIX/app"
"$PYTHON" -m venv "$PREFIX/venv"
if grep -q -- '--hash=' "$REPO/requirements.txt"; then
    "$PREFIX/venv/bin/pip" install --quiet --no-cache-dir --require-hashes -r "$REPO/requirements.txt"
else
    "$PREFIX/venv/bin/pip" install --quiet --no-cache-dir -r "$REPO/requirements.txt"
fi
# The service can't write to $PREFIX, so compile bytecode now.
"$PREFIX/venv/bin/python" -m compileall -q "$PREFIX/app" >/dev/null

# ── Optional: Bitwarden CLI for the Vaultwarden worker ───────────────────────
if [ "$with_bw" = 1 ]; then
    bw_version=$(sed -n 's/^ARG BW_CLI_VERSION=//p' "$REPO/Dockerfile")
    [ -n "$bw_version" ] || die "could not read BW_CLI_VERSION from Dockerfile"
    if command -v npm >/dev/null 2>&1; then
        npm_cache=$(mktemp -d)
        rm -rf "$PREFIX/bw"
        npm install --global --prefix "$PREFIX/bw" --cache "$npm_cache" \
            --no-fund --no-audit --loglevel=error "@bitwarden/cli@$bw_version"
        rm -rf "$npm_cache"
        echo "Installed Bitwarden CLI $bw_version to $PREFIX/bw"
    else
        echo "warning: npm not found, skipping the Bitwarden CLI. The Vaultwarden" >&2
        echo "         backup needs it: install Node.js + npm, then re-run with --with-bitwarden." >&2
    fi
fi

# ── systemd unit ─────────────────────────────────────────────────────────────
install -m 644 "$REPO/scripts/$NAME.service" "$UNIT"
systemctl_ daemon-reload
systemctl_ enable "$NAME"
systemctl_ restart "$NAME"

port=$(sed -n 's/^WEB_PORT=["'\'']\{0,1\}\([0-9]*\).*/\1/p' "$CONF_DIR/.env" | tail -n 1)
ip=$(hostname -I 2>/dev/null | cut -d' ' -f1)
cat <<EOF

Homelab Takeout is installed and running.

  GUI:      http://${ip:-localhost}:${port:-9100}
  Config:   $CONF_DIR/services.json and $CONF_DIR/.env (editable from the GUI)
  Backups:  $DATA_DIR/backups
  Logs:     journalctl -u $NAME -f   (per-run logs in $LOG_DIR)

Next steps:
  1. Open the GUI and fill in the URLs and API keys of the services you back up.
  2. Using a hostname instead of an IP? Add it to ALLOWED_HOSTS in the GUI or $CONF_DIR/.env.
  3. The GUI has no login: keep it on your LAN or behind an auth proxy.
EOF
if [ ! -x "$PREFIX/bw/bin/bw" ]; then
    echo "  4. Backing up Vaultwarden? Re-run with --with-bitwarden (needs Node.js + npm)."
fi
