#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import socket
import time
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

SOCKET_PATH = Path.home() / ".pi" / "agent" / "statusd.sock"
CONFIG_PATH = Path.home() / ".pi" / "agent" / "statusd-http.json"


def load_config() -> dict[str, Any]:
    cfg: dict[str, Any] = {}
    try:
        if CONFIG_PATH.exists():
            raw = json.loads(CONFIG_PATH.read_text())
            if isinstance(raw, dict):
                cfg = raw
    except Exception:
        cfg = {}

    host = os.environ.get("PI_STATUSD_HTTP_HOST", str(cfg.get("host") or "0.0.0.0"))
    port = int(os.environ.get("PI_STATUSD_HTTP_PORT", str(cfg.get("port") or 8787)))
    token = os.environ.get("PI_STATUSD_HTTP_TOKEN", str(cfg.get("token") or "")).strip() or None

    cidr_raw = os.environ.get("PI_STATUSD_HTTP_ALLOW_CIDRS", "")
    if cidr_raw.strip():
        allow_cidrs = [c.strip() for c in cidr_raw.split(",") if c.strip()]
    else:
        allow_cidrs = cfg.get("allow_cidrs") if isinstance(cfg.get("allow_cidrs"), list) else []

    allow_loopback_unauth = bool(cfg.get("allow_loopback_unauth", True))
    send_rate_per_10s = int(cfg.get("send_rate_per_10s", 12))

    return {
        "host": host,
        "port": max(1, min(65535, port)),
        "token": token,
        "allow_cidrs": allow_cidrs,
        "allow_loopback_unauth": allow_loopback_unauth,
        "send_rate_per_10s": max(1, min(200, send_rate_per_10s)),
    }


def request_socket(req: str) -> dict[str, Any]:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(str(SOCKET_PATH))
    s.sendall((req.strip() + "\n").encode("utf-8"))

    chunks: list[bytes] = []
    while True:
        data = s.recv(65535)
        if not data:
            break
        chunks.append(data)
        if b"\n" in data:
            break
    s.close()

    payload = b"".join(chunks).decode("utf-8", errors="ignore").strip()
    return json.loads(payload) if payload else {"ok": False, "error": "empty daemon response"}


class RateLimiter:
    def __init__(self, limit: int = 12) -> None:
        self.limit = limit
        self.recent: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.time()
        q = self.recent[key]
        while q and (now - q[0]) > 10.0:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        return True


class Handler(BaseHTTPRequestHandler):
    server_version = "pi-statusd-http/0.1"

    def _json(self, code: int, payload: dict[str, Any]) -> None:
        data = (json.dumps(payload) + "\n").encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _client_ip(self) -> str:
        return self.client_address[0] if self.client_address else ""

    def _is_loopback(self, ip: str) -> bool:
        try:
            return ipaddress.ip_address(ip).is_loopback
        except Exception:
            return False

    def _cidr_allowed(self, ip: str) -> bool:
        cidrs = getattr(self.server, "allow_cidrs", [])
        if not cidrs:
            return True
        try:
            addr = ipaddress.ip_address(ip)
        except Exception:
            return False
        for c in cidrs:
            try:
                if addr in ipaddress.ip_network(c, strict=False):
                    return True
            except Exception:
                continue
        return False

    def _authorized(self) -> bool:
        ip = self._client_ip()
        if not self._cidr_allowed(ip):
            return False

        token = getattr(self.server, "token", None)
        allow_loopback_unauth = bool(getattr(self.server, "allow_loopback_unauth", True))

        if allow_loopback_unauth and self._is_loopback(ip):
            return True

        if not token:
            return False

        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[len("Bearer "):].strip() == token
        return self.headers.get("X-Statusd-Token", "").strip() == token

    def _require_auth(self) -> bool:
        if self._authorized():
            return True
        self._json(401, {"ok": False, "error": "unauthorized"})
        return False

    def log_message(self, format: str, *args: Any) -> None:
        # Keep logs concise in daemon log file.
        print(f"[statusd-http] {self.address_string()} - {format % args}", flush=True)

    def do_GET(self) -> None:
        if not self._require_auth():
            return

        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        query = parse_qs(parsed.query)

        if path == "" or path == "/":
            self._json(200, {"ok": True, "service": "pi-statusd-http"})
            return

        if path == "/health":
            self._json(200, {"ok": True, "pong": True, "timestamp": int(time.time())})
            return

        if path == "/status":
            try:
                self._json(200, request_socket("status"))
            except Exception as e:
                self._json(502, {"ok": False, "error": f"daemon unavailable: {e}"})
            return

        if path.startswith("/latest/"):
            pid_s = path.split("/")[-1]
            try:
                pid = int(pid_s)
            except ValueError:
                self._json(400, {"ok": False, "error": "invalid pid"})
                return
            try:
                self._json(200, request_socket(f"latest {pid}"))
            except Exception as e:
                self._json(502, {"ok": False, "error": f"daemon unavailable: {e}"})
            return

        if path == "/watch":
            timeout_ms = 20000
            fingerprint = ""
            if "timeout_ms" in query:
                try:
                    timeout_ms = max(250, min(60000, int(query.get("timeout_ms", ["20000"])[0])))
                except Exception:
                    timeout_ms = 20000
            if "fingerprint" in query:
                fingerprint = str(query.get("fingerprint", [""])[0])

            cmd = "watch" if not fingerprint else f"watch {timeout_ms} {fingerprint}"
            if not fingerprint:
                cmd = f"watch {timeout_ms}"

            try:
                self._json(200, request_socket(cmd))
            except Exception as e:
                self._json(502, {"ok": False, "error": f"daemon unavailable: {e}"})
            return

        self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        if not self._require_auth():
            return

        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path != "/send":
            self._json(404, {"ok": False, "error": "not found"})
            return

        limiter: RateLimiter = getattr(self.server, "send_limiter")
        ip = self._client_ip()
        if not limiter.allow(ip):
            self._json(429, {"ok": False, "error": "send rate limit exceeded"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > 100_000:
            self._json(400, {"ok": False, "error": "invalid body"})
            return

        raw = self.rfile.read(length)
        try:
            body = json.loads(raw.decode("utf-8", errors="ignore"))
        except Exception:
            self._json(400, {"ok": False, "error": "invalid json"})
            return

        pid = body.get("pid")
        msg = body.get("message")
        if not isinstance(pid, int) or pid <= 0:
            self._json(400, {"ok": False, "error": "invalid pid"})
            return
        if not isinstance(msg, str):
            self._json(400, {"ok": False, "error": "invalid message"})
            return

        cleaned = " ".join(msg.replace("\n", " ").split()).strip()
        if not cleaned:
            self._json(400, {"ok": False, "error": "message is empty"})
            return
        if len(cleaned) > 4000:
            self._json(400, {"ok": False, "error": "message too long"})
            return

        try:
            self._json(200, request_socket(f"send {pid} {cleaned}"))
        except Exception as e:
            self._json(502, {"ok": False, "error": f"daemon unavailable: {e}"})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", type=str, default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    cfg = load_config()
    host = args.host or cfg["host"]
    port = int(args.port or cfg["port"])

    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.token = cfg.get("token")
    httpd.allow_cidrs = cfg.get("allow_cidrs") or []
    httpd.allow_loopback_unauth = bool(cfg.get("allow_loopback_unauth", True))
    httpd.send_limiter = RateLimiter(limit=int(cfg.get("send_rate_per_10s", 12)))

    print(
        f"[statusd-http] listening on {host}:{port} token={'set' if httpd.token else 'unset'} cidrs={httpd.allow_cidrs or 'any'}",
        flush=True,
    )
    httpd.serve_forever()


if __name__ == "__main__":
    main()
