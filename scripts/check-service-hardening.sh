#!/bin/sh
# Fail if the systemd unit's exposure score is above a threshold.
# Needs systemd-analyze >= 250 (offline mode); does not need systemd running.
# Usage: scripts/check-service-hardening.sh [max-score, default 3.0]
set -eu

max="${1:-3.0}"
unit="$(dirname "$0")/homelab-takeout.service"

# systemd-analyze's --threshold is the score x10 as an integer (default 100 = 10.0).
threshold=$(awk -v m="$max" 'BEGIN { printf "%d", m * 10 }')

systemd-analyze security --offline=yes --threshold="$threshold" "$unit"
