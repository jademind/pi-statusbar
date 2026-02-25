#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

INSTALL_BRIDGE=0

usage() {
  cat <<EOF
Usage: daemon/refresh-all.sh [--bridge]

What it does:
  1) Restarts pi-statusd cleanly
  2) Restarts PiStatusBar LaunchAgent app
  3) Verifies daemon socket health

Options:
  --bridge    Also run: pi install npm:@jademind/pi-bridge
              (You still need to restart active Pi sessions for extension reload.)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bridge)
      INSTALL_BRIDGE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      usage
      exit 1
      ;;
  esac
done

echo "[1/4] Restarting pi-statusd cleanly..."
pkill -f "daemon/pi_statusd.py" 2>/dev/null || true
sleep 0.4
"$ROOT_DIR/daemon/statusdctl" start

echo "[2/4] Checking daemon health..."
"$ROOT_DIR/daemon/statusdctl" status

echo "[3/4] Restarting status bar app..."
"$ROOT_DIR/daemon/statusbar-app-service" restart

if [[ "$INSTALL_BRIDGE" -eq 1 ]]; then
  echo "[4/4] Installing latest published pi-bridge package..."
  if command -v pi >/dev/null 2>&1; then
    pi install npm:@jademind/pi-bridge
  else
    echo "pi CLI not found on PATH; skipping bridge install."
  fi
else
  echo "[4/4] Bridge install skipped (pass --bridge to include it)."
fi

echo
echo "Done."
echo "If bridge was updated, restart each active Pi session and run /pi-bridge-status to verify."
