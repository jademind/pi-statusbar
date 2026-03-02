#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CLI="$ROOT_DIR/daemon/pi-statusbar"
QR_GENERATOR="$ROOT_DIR/generate_app_connect_qr.py"
# Always resolve dependencies from this repo's pyproject, even when we cd into --out-dir.
PYTHON_CMD=(uv run --project "$ROOT_DIR" python)

HOST_OVERRIDE=""
HTTP_PORT_OVERRIDE=""
HTTPS_PORT_OVERRIDE=""
TOKEN_OVERRIDE=""
OUT_DIR="$ROOT_DIR"
NO_QR=0
RESTART_DAEMON=1

usage() {
  cat <<EOF
Usage: daemon/setup-vm.sh [options]

Sets up pi-statusd + HTTP bridge on Linux and generates iOS App Connect files.

Options:
  --host <ip-or-hostname>   Override host for baseURL (default: Tailscale IPv4, else localhost)
  --http-port <port>        Persist HTTP port override (default 8787)
  --https-port <port>       Persist HTTPS port override (default 8788)
  --token <token>           Set a specific HTTP bearer token (default: keep/generate)
  --out-dir <dir>           Output directory for generated files (default: repo root)
  --no-qr                   Skip QR image generation
  --no-restart              Do not restart daemon; only ensure it's running
  -h, --help                Show this help

Generated files:
  <out-dir>/app_connect_payload.json
  <out-dir>/app_connect.env
  <out-dir>/app_connect_qr.png (if qrcode dependency is available and --no-qr not set)
EOF
}

valid_port() {
  local v="$1"
  [[ "$v" =~ ^[0-9]+$ ]] && (( v >= 1 && v <= 65535 ))
}

resolve_host() {
  local ts_ip=""

  if command -v tailscale >/dev/null 2>&1; then
    ts_ip="$(tailscale ip -4 2>/dev/null | awk 'NF {print; exit}')"
    if [[ -n "$ts_ip" ]]; then
      echo "$ts_ip"
      return 0
    fi
  fi

  echo "localhost"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host)
      HOST_OVERRIDE="${2:-}"
      [[ -n "$HOST_OVERRIDE" ]] || { echo "Missing value for --host"; exit 2; }
      shift 2
      ;;
    --http-port)
      HTTP_PORT_OVERRIDE="${2:-}"
      valid_port "$HTTP_PORT_OVERRIDE" || { echo "Invalid --http-port: ${HTTP_PORT_OVERRIDE:-<empty>}"; exit 2; }
      shift 2
      ;;
    --https-port)
      HTTPS_PORT_OVERRIDE="${2:-}"
      valid_port "$HTTPS_PORT_OVERRIDE" || { echo "Invalid --https-port: ${HTTPS_PORT_OVERRIDE:-<empty>}"; exit 2; }
      shift 2
      ;;
    --token)
      TOKEN_OVERRIDE="${2:-}"
      [[ -n "$TOKEN_OVERRIDE" ]] || { echo "Missing value for --token"; exit 2; }
      shift 2
      ;;
    --out-dir)
      OUT_DIR="${2:-}"
      [[ -n "$OUT_DIR" ]] || { echo "Missing value for --out-dir"; exit 2; }
      shift 2
      ;;
    --no-qr)
      NO_QR=1
      shift
      ;;
    --no-restart)
      RESTART_DAEMON=0
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

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required but was not found on PATH."
  exit 1
fi

if ! "${PYTHON_CMD[@]}" -V >/dev/null 2>&1; then
  echo "uv run python failed. Ensure your uv environment is initialized correctly."
  exit 1
fi

if ! command -v openssl >/dev/null 2>&1; then
  echo "openssl is required (for HTTPS cert generation) but was not found on PATH."
  exit 1
fi

mkdir -p "$OUT_DIR"

PORT_FLAGS=()
if [[ -n "$HTTP_PORT_OVERRIDE" ]]; then
  PORT_FLAGS+=(--http-port "$HTTP_PORT_OVERRIDE")
fi
if [[ -n "$HTTPS_PORT_OVERRIDE" ]]; then
  PORT_FLAGS+=(--https-port "$HTTPS_PORT_OVERRIDE")
fi

echo "[1/7] Preparing daemon..."
if [[ "$RESTART_DAEMON" -eq 1 ]]; then
  "$CLI" daemon-restart
else
  "$CLI" daemon-ensure
fi
"$CLI" daemon-status >/dev/null

echo "[2/7] Preparing HTTP token..."
if [[ -n "$TOKEN_OVERRIDE" ]]; then
  token="$("$CLI" "${PORT_FLAGS[@]}" http-token "$TOKEN_OVERRIDE" | tr -d '\r\n')"
else
  token="$("$CLI" "${PORT_FLAGS[@]}" http-token | tr -d '\r\n')"
fi

if [[ -z "$token" ]]; then
  echo "Failed to get HTTP token."
  exit 1
fi

echo "[3/7] Restarting HTTP bridge..."
"$CLI" "${PORT_FLAGS[@]}" http-restart
"$CLI" http-status >/dev/null

echo "[4/7] Reading active ports and TLS fingerprint..."
ports_json="$("$CLI" http-ports)"
read -r http_port https_port < <("${PYTHON_CMD[@]}" - <<'PY' "$ports_json"
import json
import sys
obj = json.loads(sys.argv[1])
print(int(obj.get("httpPort", 8787)), int(obj.get("httpsPort", 8788)))
PY
)

fingerprint="$("$CLI" http-cert-fingerprint | tr -d '\r\n')"

if [[ -n "$HOST_OVERRIDE" ]]; then
  host="$HOST_OVERRIDE"
else
  host="$(resolve_host)"
fi

http_base_url="http://${host}:${http_port}"
https_base_url="https://${host}:${https_port}"
if [[ -n "$fingerprint" ]]; then
  base_url="$https_base_url"
else
  base_url="$http_base_url"
fi

echo "[5/7] Writing App Connect payload files..."
payload_path="$OUT_DIR/app_connect_payload.json"
env_path="$OUT_DIR/app_connect.env"

"${PYTHON_CMD[@]}" - <<'PY' "$payload_path" "$base_url" "$token" "$http_base_url" "$fingerprint"
import json
import pathlib
import sys

out = pathlib.Path(sys.argv[1])
base_url = sys.argv[2]
token = sys.argv[3]
insecure = sys.argv[4]
fingerprint = sys.argv[5]

payload = {
    "baseURL": base_url,
    "token": token,
    "insecureFallbackBaseURL": insecure,
}
if fingerprint:
    payload["tlsCertSHA256"] = fingerprint

out.write_text(json.dumps(payload, indent=2) + "\n")
PY

cat > "$env_path" <<EOF
PI_STATUSD_BASE_URL=$base_url
PI_STATUSD_INSECURE_FALLBACK_BASE_URL=$http_base_url
PI_STATUSD_TOKEN=$token
PI_STATUSD_TLS_CERT_SHA256=$fingerprint
EOF

qr_path="$OUT_DIR/app_connect_qr.png"
qr_generated="no"
if [[ "$NO_QR" -eq 0 ]]; then
  if "${PYTHON_CMD[@]}" -c "import qrcode" >/dev/null 2>&1; then
    if [[ -n "$fingerprint" ]]; then
      (cd "$OUT_DIR" && "${PYTHON_CMD[@]}" "$QR_GENERATOR" "$base_url" "$token" "$fingerprint" >/dev/null)
    else
      (cd "$OUT_DIR" && "${PYTHON_CMD[@]}" "$QR_GENERATOR" "$base_url" "$token" >/dev/null)
    fi
    qr_generated="yes"
  fi
fi

echo "[6/7] Local bridge health check..."
if [[ "$base_url" == https://* ]]; then
  curl -fsSk -H "Authorization: Bearer $token" "$base_url/health" >/dev/null
else
  curl -fsS -H "Authorization: Bearer $token" "$base_url/health" >/dev/null
fi

echo "[7/7] Done."
echo
echo "App Connect"
echo "  baseURL:                $base_url"
echo "  insecureFallbackBaseURL:$http_base_url"
echo "  token:                  $token"
if [[ -n "$fingerprint" ]]; then
  echo "  tlsCertSHA256:          $fingerprint"
else
  echo "  tlsCertSHA256:          (none)"
fi
echo
echo "Generated"
echo "  $payload_path"
echo "  $env_path"
if [[ "$qr_generated" == "yes" ]]; then
  echo "  $qr_path"
else
  echo "  QR: not generated (install deps with: uv sync)"
fi
echo
echo "Tip: if your iPhone is on a different network, pass --host <reachable-ip-or-dns>."
