#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CLI="$ROOT_DIR/daemon/pi-statusbar"
STATE_DIR="${PI_STATUSBAR_STATE_DIR:-$HOME/.pi-statubar}"

OUT_DIR=""
PURGE_RUNTIME=0
PURGE_LOGS=0
PURGE_HTTP=0

usage() {
  cat <<EOF
Usage: daemon/teardown-vm.sh [options]

Stops pi-statusd + HTTP bridge for Linux VM / Pi Pulse setups.

Options:
  --out-dir <dir>       Remove generated App Connect files from this directory
                        (app_connect_payload.json, app_connect.env, app_connect_qr.png)
  --purge-runtime       Remove runtime state files (pid/sock)
  --purge-logs          Remove daemon + HTTP log files
  --purge-http          Remove HTTP config + TLS cert/key + token config
  --purge-all           Equivalent to: --purge-runtime --purge-logs --purge-http
  -h, --help            Show this help

Examples:
  ./daemon/teardown-vm.sh
  ./daemon/teardown-vm.sh --out-dir ~/pi-statusbar-ios
  ./daemon/teardown-vm.sh --out-dir ~/pi-statusbar-ios --purge-all
EOF
}

remove_if_exists() {
  local path="$1"
  if [[ -e "$path" ]]; then
    rm -f "$path"
    echo "  removed: $path"
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out-dir)
      OUT_DIR="${2:-}"
      [[ -n "$OUT_DIR" ]] || { echo "Missing value for --out-dir"; exit 2; }
      shift 2
      ;;
    --purge-runtime)
      PURGE_RUNTIME=1
      shift
      ;;
    --purge-logs)
      PURGE_LOGS=1
      shift
      ;;
    --purge-http)
      PURGE_HTTP=1
      shift
      ;;
    --purge-all)
      PURGE_RUNTIME=1
      PURGE_LOGS=1
      PURGE_HTTP=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      usage
      exit 2
      ;;
  esac
done

if [[ ! -x "$CLI" ]]; then
  echo "pi-statusbar CLI not found or not executable: $CLI"
  exit 1
fi

echo "[1/4] Stopping HTTP bridge..."
"$CLI" http-stop >/dev/null 2>&1 || true

echo "[2/4] Stopping daemon..."
"$CLI" daemon-stop >/dev/null 2>&1 || true

echo "[3/4] Verifying stop state..."
if "$CLI" daemon-status >/dev/null 2>&1; then
  echo "  warning: daemon still appears to be running"
else
  echo "  daemon: stopped"
fi
if "$CLI" http-status >/dev/null 2>&1; then
  echo "  warning: http bridge still appears to be running"
else
  echo "  http bridge: stopped"
fi

echo "[4/4] Optional cleanup..."
if [[ "$PURGE_RUNTIME" -eq 1 ]]; then
  remove_if_exists "$STATE_DIR/statusd.pid"
  remove_if_exists "$STATE_DIR/statusd-http.pid"
  remove_if_exists "$STATE_DIR/statusd.sock"
fi

if [[ "$PURGE_LOGS" -eq 1 ]]; then
  remove_if_exists "$STATE_DIR/statusd.log"
  remove_if_exists "$STATE_DIR/statusd-http.log"
fi

if [[ "$PURGE_HTTP" -eq 1 ]]; then
  remove_if_exists "$STATE_DIR/statusd-http.json"
  remove_if_exists "$STATE_DIR/statusd-http-cert.pem"
  remove_if_exists "$STATE_DIR/statusd-http-key.pem"
fi

if [[ -n "$OUT_DIR" ]]; then
  remove_if_exists "$OUT_DIR/app_connect_payload.json"
  remove_if_exists "$OUT_DIR/app_connect.env"
  remove_if_exists "$OUT_DIR/app_connect_qr.png"
fi

echo
echo "Done."
