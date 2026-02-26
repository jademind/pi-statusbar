#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import socket
import ssl
import subprocess
import sys
import threading
import time
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

SOCKET_PATH = Path.home() / ".pi" / "agent" / "statusd.sock"
CONFIG_PATH = Path.home() / ".pi" / "agent" / "statusd-http.json"
DEFAULT_CERT_PATH = Path.home() / ".pi" / "agent" / "statusd-http-cert.pem"
DEFAULT_KEY_PATH = Path.home() / ".pi" / "agent" / "statusd-http-key.pem"
DEFAULT_HTTP_PORT = 8787
DEFAULT_HTTPS_PORT = 8788


def _expand_path(value: str | None, fallback: Path) -> Path:
    raw = (value or "").strip()
    if not raw:
        return fallback
    return Path(raw).expanduser()


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
    port = int(os.environ.get("PI_STATUSD_HTTP_PORT", str(cfg.get("port") or DEFAULT_HTTP_PORT)))
    token = os.environ.get("PI_STATUSD_HTTP_TOKEN", str(cfg.get("token") or "")).strip() or None

    cidr_raw = os.environ.get("PI_STATUSD_HTTP_ALLOW_CIDRS", "")
    if cidr_raw.strip():
        allow_cidrs = [c.strip() for c in cidr_raw.split(",") if c.strip()]
    else:
        allow_cidrs = cfg.get("allow_cidrs") if isinstance(cfg.get("allow_cidrs"), list) else []

    allow_loopback_unauth = bool(cfg.get("allow_loopback_unauth", True))
    send_rate_per_10s = int(cfg.get("send_rate_per_10s", 12))

    https_enabled = bool(cfg.get("https_enabled", True))
    https_host = str(cfg.get("https_host") or host)
    https_port = int(cfg.get("https_port") or DEFAULT_HTTPS_PORT)
    https_cert_path = _expand_path(str(cfg.get("https_cert_path") or ""), DEFAULT_CERT_PATH)
    https_key_path = _expand_path(str(cfg.get("https_key_path") or ""), DEFAULT_KEY_PATH)

    return {
        "host": host,
        "port": max(1, min(65535, port)),
        "token": token,
        "allow_cidrs": allow_cidrs,
        "allow_loopback_unauth": allow_loopback_unauth,
        "send_rate_per_10s": max(1, min(200, send_rate_per_10s)),
        "https_enabled": https_enabled,
        "https_host": https_host,
        "https_port": max(1, min(65535, https_port)),
        "https_cert_path": https_cert_path,
        "https_key_path": https_key_path,
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


def ensure_self_signed_cert(cert_path: Path, key_path: Path) -> None:
    if cert_path.exists() and key_path.exists():
        return

    cert_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "openssl",
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-sha256",
        "-days",
        "3650",
        "-keyout",
        str(key_path),
        "-out",
        str(cert_path),
        "-subj",
        "/CN=pi-statusd-http",
        "-addext",
        "subjectAltName=DNS:localhost,IP:127.0.0.1",
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def cert_fingerprint_sha256(cert_path: Path) -> str | None:
    try:
        pem = cert_path.read_text()
        der = ssl.PEM_cert_to_DER_cert(pem)
        digest = hashlib.sha256(der).hexdigest().upper()
        return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))
    except Exception:
        return None


def _agent_message_id(agent: dict[str, Any]) -> str | None:
    text = str(agent.get("latest_message_full") or agent.get("latest_message") or "").strip()
    at = str(agent.get("latest_message_at") or "").strip()
    if not text and not at:
        return None
    raw = f"{agent.get('pid')}|{at}|{text}"
    return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _agent_fingerprint(agent: dict[str, Any]) -> str:
    compact = {
        "pid": agent.get("pid"),
        "activity": agent.get("activity"),
        "latest_message_id": agent.get("latest_message_id"),
    }
    return hashlib.sha1(json.dumps(compact, sort_keys=True).encode("utf-8")).hexdigest()


def _status_fingerprint(status: dict[str, Any]) -> str:
    agents = status.get("agents") if isinstance(status, dict) else []
    if not isinstance(agents, list):
        agents = []
    compact = []
    for item in agents:
        if not isinstance(item, dict):
            continue
        compact.append(
            {
                "pid": item.get("pid"),
                "activity": item.get("activity"),
                "latest_message_id": item.get("latest_message_id"),
            }
        )
    compact.sort(key=lambda x: int(x.get("pid") or 0))
    return hashlib.sha1(json.dumps(compact, sort_keys=True).encode("utf-8")).hexdigest()


def _normalize_status_payload(raw: dict[str, Any]) -> dict[str, Any]:
    out = dict(raw) if isinstance(raw, dict) else {"ok": False, "error": "invalid status payload"}
    raw_agents = out.get("agents") if isinstance(out.get("agents"), list) else []
    agents: list[dict[str, Any]] = []
    for item in raw_agents:
        if not isinstance(item, dict):
            continue
        a = dict(item)
        a["latest_message_id"] = _agent_message_id(a)
        agents.append(a)
    out["agents"] = agents
    out["fingerprint"] = _status_fingerprint(out)
    return out


def _find_agent(status: dict[str, Any], pid: int) -> dict[str, Any] | None:
    agents = status.get("agents") if isinstance(status.get("agents"), list) else []
    for item in agents:
        if isinstance(item, dict) and int(item.get("pid") or 0) == pid:
            return item
    return None


def _classify_agent_event(prev: dict[str, Any], curr: dict[str, Any]) -> str:
    if prev.get("latest_message_id") != curr.get("latest_message_id"):
        return "message_updated"
    if prev.get("activity") != curr.get("activity"):
        return "activity_changed"
    return "agent_updated"


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


class SafeThreadingHTTPServer(ThreadingHTTPServer):
    def handle_error(self, request: Any, client_address: tuple[str, int]) -> None:
        _typ, err, _tb = sys.exc_info()
        if isinstance(err, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError)):
            print(f"[statusd-http] client disconnected {client_address[0]}:{client_address[1]} ({err.__class__.__name__})", flush=True)
            return
        if isinstance(err, ssl.SSLError):
            msg = str(err).lower()
            if "eof occurred" in msg or "tlsv1 alert" in msg or "wrong version number" in msg:
                print(f"[statusd-http] tls disconnect {client_address[0]}:{client_address[1]} ({err})", flush=True)
                return
        super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    server_version = "pi-statusd-http/0.3"
    protocol_version = "HTTP/1.1"

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
        print(f"[statusd-http] {self.address_string()} - {format % args}", flush=True)

    def _parse_timeout_ms(self, query: dict[str, list[str]], default: int = 20000) -> int:
        try:
            raw = str((query.get("timeout_ms") or [str(default)])[0])
            return max(250, min(60000, int(raw)))
        except Exception:
            return default

    def _load_status(self) -> dict[str, Any]:
        return _normalize_status_payload(request_socket("status"))

    def _watch_global(self, query: dict[str, list[str]]) -> None:
        timeout_ms = self._parse_timeout_ms(query, default=20000)
        since = str((query.get("fingerprint") or [""])[0]).strip()

        start_status = self._load_status()
        start_fp = str(start_status.get("fingerprint") or "")

        if not since:
            self._json(200, {"ok": True, "event": "snapshot", "fingerprint": start_fp, "status": start_status})
            return

        if since != start_fp:
            self._json(200, {"ok": True, "event": "out_of_sync", "fingerprint": start_fp, "status": start_status})
            return

        deadline = time.time() + (timeout_ms / 1000.0)
        while time.time() < deadline:
            time.sleep(0.6)
            curr = self._load_status()
            curr_fp = str(curr.get("fingerprint") or "")
            if curr_fp != start_fp:
                changes: list[dict[str, Any]] = []
                prev_agents = {int(a.get("pid") or 0): a for a in (start_status.get("agents") or []) if isinstance(a, dict)}
                curr_agents = {int(a.get("pid") or 0): a for a in (curr.get("agents") or []) if isinstance(a, dict)}

                for pid, agent in curr_agents.items():
                    before = prev_agents.get(pid)
                    if before is None:
                        changes.append({"event": "activity_changed", "pid": pid, "activity": agent.get("activity")})
                        if agent.get("latest_message_id"):
                            ev = {"event": "message_updated", "pid": pid, "latest_message_id": agent.get("latest_message_id")}
                            if agent.get("latest_message"):
                                ev["latest_message"] = agent.get("latest_message")
                            changes.append(ev)
                        continue
                    if before.get("activity") != agent.get("activity"):
                        changes.append({"event": "activity_changed", "pid": pid, "activity": agent.get("activity")})
                    if before.get("latest_message_id") != agent.get("latest_message_id"):
                        ev = {
                            "event": "message_updated",
                            "pid": pid,
                            "latest_message_id": agent.get("latest_message_id"),
                            "latest_message_at": agent.get("latest_message_at"),
                        }
                        if agent.get("latest_message"):
                            ev["latest_message"] = agent.get("latest_message")
                        changes.append(ev)

                self._json(
                    200,
                    {
                        "ok": True,
                        "event": "status_changed",
                        "fingerprint": curr_fp,
                        "changes": changes,
                        "status": curr,
                    },
                )
                return

        self._json(200, {"ok": True, "event": "timeout", "fingerprint": start_fp})

    def _watch_pid_long_poll(self, pid: int, query: dict[str, list[str]]) -> None:
        timeout_ms = self._parse_timeout_ms(query, default=20000)
        since = str((query.get("fingerprint") or [""])[0]).strip()

        status0 = self._load_status()
        agent0 = _find_agent(status0, pid)
        if not agent0:
            self._json(404, {"ok": False, "error": "pid not found"})
            return
        fp0 = _agent_fingerprint(agent0)

        if not since:
            self._json(200, {"ok": True, "event": "snapshot", "pid": pid, "fingerprint": fp0, "agent": agent0})
            return

        if since != fp0:
            self._json(200, {"ok": True, "event": "out_of_sync", "pid": pid, "fingerprint": fp0, "agent": agent0})
            return

        deadline = time.time() + (timeout_ms / 1000.0)
        while time.time() < deadline:
            time.sleep(0.6)
            curr_status = self._load_status()
            curr_agent = _find_agent(curr_status, pid)
            if not curr_agent:
                self._json(200, {"ok": True, "event": "agent_gone", "pid": pid})
                return

            curr_fp = _agent_fingerprint(curr_agent)
            if curr_fp != fp0:
                ev = _classify_agent_event(agent0, curr_agent)
                self._json(200, {"ok": True, "event": ev, "pid": pid, "fingerprint": curr_fp, "agent": curr_agent})
                return

        self._json(200, {"ok": True, "event": "timeout", "pid": pid, "fingerprint": fp0})

    def _watch_pid_sse(self, pid: int) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        seq = 0

        def send_event(name: str, payload: dict[str, Any], event_id: str | None = None) -> None:
            nonlocal seq
            seq += 1
            eid = event_id or f"{int(time.time() * 1000)}-{seq}"
            blob = json.dumps(payload, separators=(",", ":"))
            self.wfile.write(f"id: {eid}\n".encode("utf-8"))
            self.wfile.write(f"event: {name}\n".encode("utf-8"))
            self.wfile.write(f"data: {blob}\n\n".encode("utf-8"))
            self.wfile.flush()

        status = self._load_status()
        agent = _find_agent(status, pid)
        if not agent:
            send_event("error", {"ok": False, "error": "pid not found", "pid": pid}, event_id=f"{pid}:error")
            return

        prev = agent
        prev_fp = _agent_fingerprint(prev)
        current_id = f"{pid}:{prev_fp}"
        last_event_id = (self.headers.get("Last-Event-ID", "") or "").strip()

        # Resume-friendly bootstrap:
        # - no Last-Event-ID  -> snapshot
        # - same Last-Event-ID -> suppress duplicate snapshot and continue watching
        # - different          -> out_of_sync with current snapshot payload
        if not last_event_id:
            send_event("snapshot", {"ok": True, "pid": pid, "fingerprint": prev_fp, "agent": prev}, event_id=current_id)
        elif last_event_id != current_id:
            send_event("out_of_sync", {"ok": True, "pid": pid, "fingerprint": prev_fp, "agent": prev}, event_id=current_id)

        last_keepalive = time.time()
        while True:
            time.sleep(0.6)
            try:
                curr_status = self._load_status()
                curr = _find_agent(curr_status, pid)
                if not curr:
                    send_event("agent_gone", {"ok": True, "pid": pid}, event_id=f"{pid}:gone")
                    return
                curr_fp = _agent_fingerprint(curr)
                if curr_fp != prev_fp:
                    ev = _classify_agent_event(prev, curr)
                    send_event(ev, {"ok": True, "pid": pid, "fingerprint": curr_fp, "agent": curr}, event_id=f"{pid}:{curr_fp}")
                    prev = curr
                    prev_fp = curr_fp
                if (time.time() - last_keepalive) > 15:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    last_keepalive = time.time()
            except (BrokenPipeError, ConnectionResetError):
                return

    def do_GET(self) -> None:
        if not self._require_auth():
            return

        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        query = parse_qs(parsed.query)

        if path == "" or path == "/":
            self._json(200, {"ok": True, "service": "pi-statusd-http", "api_version": 3})
            return

        if path == "/health":
            self._json(200, {"ok": True, "pong": True, "timestamp": int(time.time())})
            return

        if path == "/tls":
            self._json(
                200,
                {
                    "ok": True,
                    "https_enabled": bool(getattr(self.server, "https_enabled", False)),
                    "https_port": int(getattr(self.server, "https_port", 0)),
                    "cert_sha256": getattr(self.server, "cert_sha256", None),
                },
            )
            return

        if path == "/status":
            try:
                self._json(200, self._load_status())
            except Exception as e:
                self._json(502, {"ok": False, "error": f"daemon unavailable: {e}"})
            return

        if path == "/watch":
            try:
                self._watch_global(query)
            except Exception as e:
                self._json(502, {"ok": False, "error": f"daemon unavailable: {e}"})
            return

        if path.startswith("/watch/"):
            pid_s = path.split("/")[-1]
            try:
                pid = int(pid_s)
            except ValueError:
                self._json(400, {"ok": False, "error": "invalid pid"})
                return

            wants_sse = "text/event-stream" in (self.headers.get("Accept", "").lower())
            try:
                if wants_sse:
                    self._watch_pid_sse(pid)
                else:
                    self._watch_pid_long_poll(pid, query)
            except Exception as e:
                if wants_sse:
                    try:
                        self.wfile.write(f"event: error\ndata: {json.dumps({'ok': False, 'error': str(e)})}\n\n".encode("utf-8"))
                        self.wfile.flush()
                    except Exception:
                        pass
                else:
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


def apply_shared_server_state(server: ThreadingHTTPServer, cfg: dict[str, Any], cert_sha256: str | None) -> None:
    server.token = cfg.get("token")
    server.allow_cidrs = cfg.get("allow_cidrs") or []
    server.allow_loopback_unauth = bool(cfg.get("allow_loopback_unauth", True))
    server.send_limiter = RateLimiter(limit=int(cfg.get("send_rate_per_10s", 12)))
    server.https_enabled = bool(cfg.get("https_enabled", False))
    server.https_port = int(cfg.get("https_port", 0))
    server.cert_sha256 = cert_sha256


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", type=str, default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    cfg = load_config()
    host = args.host or cfg["host"]
    port = int(args.port or cfg["port"])

    cert_sha256: str | None = None

    # HTTP endpoint (existing behavior)
    httpd = SafeThreadingHTTPServer((host, port), Handler)
    apply_shared_server_state(httpd, cfg, cert_sha256)

    print(
        f"[statusd-http] http listening on {host}:{port} token={'set' if httpd.token else 'unset'} cidrs={httpd.allow_cidrs or 'any'}",
        flush=True,
    )

    # Optional HTTPS endpoint with self-signed cert
    https_enabled = bool(cfg.get("https_enabled", True))
    if https_enabled:
        cert_path: Path = cfg["https_cert_path"]
        key_path: Path = cfg["https_key_path"]
        try:
            ensure_self_signed_cert(cert_path, key_path)
            cert_sha256 = cert_fingerprint_sha256(cert_path)

            https_host = str(cfg.get("https_host") or host)
            https_port = int(cfg.get("https_port") or DEFAULT_HTTPS_PORT)
            httpsd = SafeThreadingHTTPServer((https_host, https_port), Handler)
            apply_shared_server_state(httpsd, cfg, cert_sha256)

            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
            httpsd.socket = context.wrap_socket(httpsd.socket, server_side=True)

            t = threading.Thread(target=httpsd.serve_forever, name="statusd-https", daemon=True)
            t.start()

            print(
                f"[statusd-http] https listening on {https_host}:{https_port} cert_sha256={cert_sha256 or 'unknown'}",
                flush=True,
            )
        except Exception as e:
            print(f"[statusd-http] https disabled (setup failed): {e}", flush=True)

    # Update HTTP server state with cert metadata too
    apply_shared_server_state(httpd, cfg, cert_sha256)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
