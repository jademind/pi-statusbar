#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import html as html_lib
import subprocess
import time
import tempfile
import pwd
import unicodedata
import fcntl
import termios
import uuid
from datetime import datetime
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List

SOCKET_PATH = Path.home() / ".pi-statubar" / "statusd.sock"
CONFIG_PATH = Path.home() / ".pi-statubar" / "statusd.json"


@dataclass
class Agent:
    pid: int
    ppid: int
    state: str
    tty: str
    cpu: float
    cwd: str | None
    activity: str
    confidence: str
    mux: str | None
    mux_session: str | None
    client_pid: int | None
    attached_window: bool | None = None
    terminal_app: str | None = None
    telemetry_source: str | None = None
    model_provider: str | None = None
    model_id: str | None = None
    model_name: str | None = None
    session_id: str | None = None
    session_name: str | None = None
    context_percent: float | None = None
    context_pressure: str | None = None
    context_close_to_limit: bool | None = None
    context_near_limit: bool | None = None
    context_tokens: int | None = None
    context_window: int | None = None
    context_remaining_tokens: int | None = None
    session_file: str | None = None
    latest_message: str | None = None
    latest_message_full: str | None = None
    latest_message_html: str | None = None
    latest_message_at: int | None = None
    extension_pi_telemetry: bool | None = None
    extension_pi_bridge: bool | None = None
    bridge_available: bool | None = None


class Scanner:
    def __init__(self) -> None:
        self._runtime_preview_cache: Dict[int, Dict] = {}
        self._session_message_cache: Dict[str, Dict] = {}

    def scan(self) -> Dict:
        rows = self._ps_rows()
        by_pid = {r["pid"]: r for r in rows}
        telemetry_instances = self._read_pi_telemetry_instances()

        process_agents = self._agents_from_processes(rows, by_pid)
        telemetry_agents = self._agents_from_telemetry(telemetry_instances, rows, by_pid) if telemetry_instances else []

        if telemetry_agents:
            merged: Dict[int, Agent] = {a.pid: a for a in process_agents}
            for t in telemetry_agents:
                merged[t.pid] = t
            agents = list(merged.values())
            used_telemetry = True
        else:
            agents = process_agents
            used_telemetry = False

        agents.sort(key=lambda a: a.pid)
        return {
            "ok": True,
            "timestamp": int(time.time()),
            "agents": [asdict(a) for a in agents],
            "summary": self._summarize(agents),
            "version": 2,
            "source": "pi-telemetry" if used_telemetry else "process-fallback",
        }

    def _agents_from_processes(self, rows: List[Dict], by_pid: Dict[int, Dict]) -> List[Agent]:
        pi_rows = [r for r in rows if r["comm"] == "pi"]
        pids = [r["pid"] for r in pi_rows]
        cwd_map = self._cwd_map(pids)

        agents: List[Agent] = []
        for row in pi_rows:
            activity, confidence = self._infer_activity(row)
            mux, mux_session = self._infer_mux(row, by_pid)
            latest_message_full = None
            latest_message = None
            client_pid = self._find_mux_client_pid(mux, mux_session, row["tty"], rows)
            terminal_app, _ = self._detect_terminal_target_for_pid(client_pid or row["pid"], by_pid)
            attached_window = client_pid is not None or (terminal_app is not None and row.get("tty") != "??")
            bridge_registry = self._bridge_registry_for_pid(row["pid"])
            agents.append(
                Agent(
                    pid=row["pid"],
                    ppid=row["ppid"],
                    state=row["state"],
                    tty=row["tty"],
                    cpu=row["cpu"],
                    cwd=cwd_map.get(row["pid"]),
                    activity=activity,
                    confidence=confidence,
                    mux=mux,
                    mux_session=mux_session,
                    client_pid=client_pid,
                    attached_window=attached_window,
                    terminal_app=terminal_app,
                    telemetry_source=None,
                    latest_message=latest_message,
                    latest_message_full=None,
                    latest_message_html=None,
                    extension_pi_telemetry=False,
                    extension_pi_bridge=bridge_registry is not None,
                    bridge_available=bridge_registry is not None,
                )
            )
        return agents

    def _agents_from_telemetry(self, telemetry_instances: List[Dict], rows: List[Dict], by_pid: Dict[int, Dict]) -> List[Agent]:
        pids: List[int] = []
        for instance in telemetry_instances:
            pid = self._to_int((instance.get("process") or {}).get("pid"))
            if pid and pid > 0:
                pids.append(pid)

        cwd_map = self._cwd_map(pids)
        agents: List[Agent] = []

        for instance in telemetry_instances:
            process = instance.get("process") or {}
            state_info = instance.get("state") or {}
            workspace = instance.get("workspace") or {}
            context = instance.get("context") or {}
            model = instance.get("model") or {}
            session = instance.get("session") or {}

            pid = self._to_int(process.get("pid"), default=0)
            if pid <= 0:
                continue

            session_file = str(session.get("file") or "").strip() or None
            telemetry_messages = instance.get("messages") or {}
            telemetry_last_text = self._clean_message_text(str(telemetry_messages.get("lastAssistantText") or ""))
            telemetry_last_html = str(telemetry_messages.get("lastAssistantHtml") or "").strip()
            telemetry_html_text = self._html_to_text(telemetry_last_html)

            latest_message_full = telemetry_last_text or telemetry_html_text or None
            latest_message_html = telemetry_last_html or None
            latest_message_at = self._extract_timestamp_ms(telemetry_messages) if isinstance(telemetry_messages, dict) else None

            latest_message = self._message_gist(latest_message_full)
            if session_file and not latest_message_full:
                parsed_text, parsed_ts = self._latest_assistant_message(session_file)
                if not latest_message_full:
                    latest_message_full = parsed_text
                if not latest_message:
                    latest_message = self._message_gist(parsed_text)
                if latest_message_at is None:
                    latest_message_at = parsed_ts

            if not latest_message_html:
                latest_message_html = self._message_html(latest_message_full)

            row = by_pid.get(pid, {})
            tty = row.get("tty") or "??"
            mux, mux_session = self._infer_mux(row, by_pid) if row else (None, None)
            client_pid = self._find_mux_client_pid(mux, mux_session, tty, rows) if row else None
            terminal_app, _ = self._detect_terminal_target_for_pid(client_pid or pid, by_pid)
            attached_window = client_pid is not None or (terminal_app is not None and tty != "??")

            extensions_info = instance.get("extensions") if isinstance(instance.get("extensions"), dict) else {}
            bridge_ext = extensions_info.get("bridge") if isinstance(extensions_info.get("bridge"), dict) else {}
            bridge_active = bridge_ext.get("active") if isinstance(bridge_ext.get("active"), bool) else None
            bridge_registry = self._bridge_registry_for_pid(pid)
            bridge_available = bool(bridge_active) or bridge_registry is not None

            agents.append(
                Agent(
                    pid=pid,
                    ppid=int(process.get("ppid") or row.get("ppid") or 0),
                    state=str(row.get("state") or "?"),
                    tty=str(tty),
                    cpu=float(row.get("cpu") or 0.0),
                    cwd=str(workspace.get("cwd") or cwd_map.get(pid) or "") or None,
                    activity=self._map_telemetry_activity(state_info),
                    confidence="high",
                    mux=mux,
                    mux_session=mux_session,
                    client_pid=client_pid,
                    attached_window=attached_window,
                    terminal_app=terminal_app,
                    telemetry_source=str(instance.get("source") or "pi-telemetry"),
                    model_provider=str(model.get("provider")) if model.get("provider") is not None else None,
                    model_id=str(model.get("id")) if model.get("id") is not None else None,
                    model_name=str(model.get("name")) if model.get("name") is not None else None,
                    session_id=str(session.get("id")) if session.get("id") is not None else None,
                    session_name=str(session.get("name")) if session.get("name") is not None else None,
                    context_percent=float(context.get("percent")) if isinstance(context.get("percent"), (int, float)) else None,
                    context_pressure=str(context.get("pressure")) if context.get("pressure") is not None else None,
                    context_close_to_limit=bool(context.get("closeToLimit")) if "closeToLimit" in context else None,
                    context_near_limit=bool(context.get("nearLimit")) if "nearLimit" in context else None,
                    context_tokens=self._to_int(context.get("tokens")),
                    context_window=self._to_int(context.get("contextWindow")),
                    context_remaining_tokens=self._to_int(context.get("remainingTokens")),
                    session_file=session_file,
                    latest_message=latest_message,
                    latest_message_full=latest_message_full,
                    latest_message_html=latest_message_html,
                    latest_message_at=latest_message_at,
                    extension_pi_telemetry=True,
                    extension_pi_bridge=bridge_active if isinstance(bridge_active, bool) else (bridge_registry is not None),
                    bridge_available=bridge_available,
                )
            )

        return agents

    def _read_pi_telemetry_instances(self) -> List[Dict]:
        telemetry_dir = Path(os.environ.get("PI_TELEMETRY_DIR", str(Path.home() / ".pi-statubar" / "telemetry" / "instances")))
        stale_ms = int(os.environ.get("PI_TELEMETRY_STALE_MS", "10000"))
        now_ms = int(time.time() * 1000)

        instances: List[Dict] = []

        if telemetry_dir.exists():
            for file in telemetry_dir.glob("*.json"):
                try:
                    data = json.loads(file.read_text())
                except Exception:
                    continue

                process = data.get("process") or {}
                pid = process.get("pid")
                updated_at = process.get("updatedAt")
                if not isinstance(pid, int) or pid <= 0:
                    continue
                if not isinstance(updated_at, (int, float)):
                    continue

                try:
                    os.kill(pid, 0)
                except Exception:
                    continue

                if now_ms - int(updated_at) > stale_ms:
                    continue

                instances.append(data)

        if instances:
            return instances

        # Optional fallback to CLI if available.
        try:
            proc = subprocess.run(["pi-telemetry-snapshot"], capture_output=True, text=True, timeout=1.2)
            if proc.returncode == 0 and proc.stdout.strip():
                payload = json.loads(proc.stdout)
                cli_instances = payload.get("instances")
                if isinstance(cli_instances, list):
                    valid: List[Dict] = []
                    for item in cli_instances:
                        if not isinstance(item, dict):
                            continue
                        process = item.get("process") or {}
                        pid = self._to_int(process.get("pid"), default=0)
                        if not pid or pid <= 0:
                            continue
                        valid.append(item)
                    return valid
        except Exception:
            pass

        return []

    def _telemetry_instance_for_pid(self, pid: int) -> Dict | None:
        for inst in self._read_pi_telemetry_instances():
            if not isinstance(inst, dict):
                continue
            process = inst.get("process")
            if not isinstance(process, dict):
                continue
            if self._to_int(process.get("pid"), default=0) == pid:
                return inst
        return None

    def _bridge_base_dir(self) -> Path:
        configured = os.environ.get("PI_BRIDGE_DIR", "").strip()
        if configured:
            return Path(configured)
        return Path.home() / ".pi-statubar" / "statusbridge"

    def _bridge_registry_for_pid(self, pid: int) -> Dict | None:
        registry_file = self._bridge_base_dir() / "registry" / f"{pid}.json"
        if not registry_file.exists() or not registry_file.is_file():
            return None
        try:
            payload = json.loads(registry_file.read_text())
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        if self._to_int(payload.get("pid"), default=0) != pid:
            return None

        updated_at = self._to_int(payload.get("updatedAt"), default=0) or 0
        stale_ms = self._to_int(os.environ.get("PI_BRIDGE_REGISTRY_STALE_MS"), default=10_000) or 10_000
        if updated_at <= 0:
            return None
        if int(time.time() * 1000) - updated_at > max(1000, stale_ms):
            return None
        try:
            os.kill(pid, 0)
        except Exception:
            return None
        return payload

    def _send_via_bridge(self, pid: int, text: str, mode: str = "queued") -> Dict | None:
        registry = self._bridge_registry_for_pid(pid)
        if not registry:
            return None

        base_dir = self._bridge_base_dir()
        inbox_dir = base_dir / "inbox" / str(pid)
        ack_dir = base_dir / "acks" / str(pid)
        try:
            inbox_dir.mkdir(parents=True, exist_ok=True)
            ack_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return {
                "ok": False,
                "pid": pid,
                "delivery": "pi-bridge",
                "error": f"bridge directory error: {e}",
            }

        timeout_ms = self._to_int(os.environ.get("PI_BRIDGE_ACK_TIMEOUT_MS"), default=1200) or 1200
        retry_attempts = self._to_int(os.environ.get("PI_BRIDGE_SEND_RETRIES"), default=3) or 3
        retry_backoff_ms = self._to_int(os.environ.get("PI_BRIDGE_SEND_RETRY_BACKOFF_MS"), default=450) or 450
        retry_attempts = max(1, min(8, retry_attempts))
        retry_backoff_ms = max(100, min(3_000, retry_backoff_ms))

        last_error: Dict | None = None

        for attempt in range(retry_attempts):
            now_ms = int(time.time() * 1000)
            expires_ms = now_ms + 60_000
            msg_id = str(uuid.uuid4())
            envelope = {
                "v": 1,
                "id": msg_id,
                "pid": pid,
                "text": text,
                "source": "statusbar",
                "createdAt": datetime.utcfromtimestamp(now_ms / 1000.0).isoformat(timespec="milliseconds") + "Z",
                "expiresAt": datetime.utcfromtimestamp(expires_ms / 1000.0).isoformat(timespec="milliseconds") + "Z",
                "delivery": {
                    "mode": "interrupt" if mode == "interrupt" else "queued",
                },
                "meta": {
                    "requestId": f"statusd-{msg_id}",
                    "attempt": attempt + 1,
                },
            }

            inbox_file = inbox_dir / f"{msg_id}.json"
            tmp_file = inbox_dir / f".{msg_id}.tmp"
            ack_file = ack_dir / f"{msg_id}.json"

            try:
                tmp_file.write_text(json.dumps(envelope))
                os.replace(tmp_file, inbox_file)
            except Exception as e:
                try:
                    if tmp_file.exists():
                        tmp_file.unlink()
                except Exception:
                    pass
                return {
                    "ok": False,
                    "pid": pid,
                    "delivery": "pi-bridge",
                    "error": f"bridge enqueue failed: {e}",
                }

            deadline = time.time() + max(0.2, timeout_ms / 1000.0)
            ack = None
            while time.time() < deadline:
                if ack_file.exists():
                    try:
                        ack = json.loads(ack_file.read_text())
                    except Exception:
                        ack = {"status": "failed", "error": "invalid_ack"}
                    break
                time.sleep(0.05)

            if ack is None:
                last_error = {
                    "ok": False,
                    "pid": pid,
                    "delivery": "pi-bridge",
                    "error": "bridge ack timeout",
                    "bridge_mode": envelope["delivery"]["mode"],
                    "bridge_attempt": attempt + 1,
                }
                break

            status = str((ack or {}).get("status") or "failed")
            if status == "delivered":
                resp = {
                    "ok": True,
                    "pid": pid,
                    "delivery": "pi-bridge",
                    "bridge_mode": (ack or {}).get("resolvedMode") or envelope["delivery"]["mode"],
                    "bridge_ack": status,
                }
                if attempt > 0:
                    resp["bridge_attempts"] = attempt + 1
                return resp

            bridge_error = str((ack or {}).get("error") or "")
            last_error = {
                "ok": False,
                "pid": pid,
                "delivery": "pi-bridge",
                "error": f"bridge ack: {status}",
                "bridge_mode": (ack or {}).get("resolvedMode") or envelope["delivery"]["mode"],
                "bridge_ack": status,
                "bridge_error": bridge_error or None,
                "bridge_attempt": attempt + 1,
            }

            should_retry = bridge_error in ("rate_limited", "bridge_rate_limited", "pi_rate_limited") and attempt < (retry_attempts - 1)
            if should_retry:
                time.sleep(retry_backoff_ms / 1000.0)
                continue
            return last_error

        return last_error or {
            "ok": False,
            "pid": pid,
            "delivery": "pi-bridge",
            "error": "bridge delivery failed",
        }

    def _tmux_target_for_tty(self, tty: str | None) -> str | None:
        if not tty or tty == "??":
            return None
        tty_path = tty if tty.startswith("/dev/") else f"/dev/{tty}"
        try:
            proc = subprocess.run(
                ["tmux", "list-panes", "-a", "-F", "#{pane_tty} #{session_name}:#{window_index}.#{pane_index}"],
                capture_output=True,
                text=True,
                timeout=1.2,
            )
            if proc.returncode != 0:
                return None
            for raw in proc.stdout.splitlines():
                line = raw.strip()
                if not line:
                    continue
                parts = line.split(" ", 1)
                if len(parts) != 2:
                    continue
                pane_tty, target = parts
                if pane_tty == tty_path:
                    return target.strip() or None
        except Exception:
            return None
        return None

    def _send_tmux_message(self, text: str, mux_session: str | None, tmux_target: str | None) -> bool:
        attempts: List[List[str]] = []

        # Best target: telemetry/tty-resolved pane target (session:window.pane).
        if tmux_target:
            attempts.append(["tmux", "send-keys", "-t", tmux_target, text, "C-m"])

        # Session-targeted send (works for regular tmux session names).
        if mux_session:
            attempts.append(["tmux", "send-keys", "-t", mux_session, text, "C-m"])
            # Compatibility fallback when mux_session is actually a socket label.
            attempts.append(["tmux", "-L", mux_session, "send-keys", text, "C-m"])

        # Final fallback: current client session.
        attempts.append(["tmux", "send-keys", text, "C-m"])

        for cmd in attempts:
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1.2)
                if proc.returncode == 0:
                    return True
            except Exception:
                continue
        return False

    def _inject_tty_input(self, tty: str, text: str) -> bool:
        tty_path = tty if tty.startswith("/dev/") else f"/dev/{tty}"
        payload = text + "\n"
        fd = -1
        try:
            fd = os.open(tty_path, os.O_RDWR | os.O_NOCTTY)
            for ch in payload:
                fcntl.ioctl(fd, termios.TIOCSTI, ch.encode("utf-8", errors="ignore"))
            return True
        except Exception:
            return False
        finally:
            if fd >= 0:
                try:
                    os.close(fd)
                except Exception:
                    pass

    def _send_via_terminal_script(self, text: str, tty: str, app_name: str | None) -> bool:
        target_tty = self._applescript_escape(tty)
        payload = self._applescript_escape(text)

        # Prefer the detected terminal app first, then try the other supported one.
        order: List[str] = []
        if app_name in ("iTerm2", "Terminal"):
            order.append(app_name)
        for candidate in ("iTerm2", "Terminal"):
            if candidate not in order:
                order.append(candidate)

        for candidate in order:
            if candidate == "iTerm2":
                script = f'''
set targetTTY to "{target_tty}"
set payload to "{payload}"
try
  tell application "iTerm2"
    repeat with w in windows
      repeat with tb in tabs of w
        repeat with s in sessions of tb
          try
            if (tty of s as text) ends with targetTTY then
              write text payload newline YES to s
              return "ok"
            end if
          end try
        end repeat
      end repeat
    end repeat
  end tell
end try
return "no"
'''
                if self._run_osascript(script) == "ok":
                    return True

            if candidate == "Terminal":
                script = f'''
set targetTTY to "{target_tty}"
set payload to "{payload}"
try
  tell application "Terminal"
    repeat with w in windows
      repeat with tb in tabs of w
        try
          if (tty of tb as text) ends with targetTTY then
            do script payload in tb
            return "ok"
          end if
        end try
      end repeat
    end repeat
  end tell
end try
return "no"
'''
                if self._run_osascript(script) == "ok":
                    return True

        return False

    def _send_via_ui_typing(
        self,
        text: str,
        app_name: str | None,
        hints: List[str],
        app_pid: int | None = None,
        tty: str | None = None,
    ) -> bool:
        if not app_name:
            return False

        focused = False
        if tty and app_name in ("iTerm2", "Terminal"):
            focused = self._focus_terminal_by_tty(tty)

        if not focused:
            focused = self._focus_terminal_app(app_name, hints, app_pid)
        if not focused and app_name == "Ghostty":
            focused = self._focus_ghostty_window_by_hints_any(hints)
        if not focused:
            focused = self._activate_app(app_name)
        if not focused:
            return False

        escaped = self._applescript_escape(text)
        script = f'''
try
  tell application "System Events"
    keystroke "{escaped}"
    key code 36
    return "ok"
  end tell
end try
return "no"
'''
        return self._run_osascript(script) == "ok"

    def _map_telemetry_activity(self, state_info: Dict | object) -> str:
        if isinstance(state_info, dict):
            activity = state_info.get("activity")
            if activity == "working":
                return "running"
            if activity == "waiting_input":
                return "waiting_input"

            # Defensive compatibility: infer from boolean state fields if activity is absent.
            if state_info.get("waitingForInput") is True:
                return "waiting_input"
            if state_info.get("busy") is True or state_info.get("isIdle") is False:
                return "running"
            if state_info.get("isIdle") is True:
                return "unknown"
            return "unknown"

        if state_info == "working":
            return "running"
        if state_info == "waiting_input":
            return "waiting_input"
        return "unknown"

    def latest_message(self, pid: int) -> Dict:
        rows = self._ps_rows()
        by_pid = {r["pid"]: r for r in rows}
        row = next((r for r in rows if r.get("pid") == pid and r.get("comm") == "pi"), None)
        if not row:
            return {"ok": False, "error": f"pi pid not found: {pid}"}

        session_file: str | None = None
        latest_at: int | None = None
        latest_full: str | None = None
        latest_html: str | None = None

        telemetry_instances = self._read_pi_telemetry_instances()
        for inst in telemetry_instances:
            proc = inst.get("process") if isinstance(inst, dict) else None
            if not isinstance(proc, dict):
                continue
            if self._to_int(proc.get("pid"), default=0) != pid:
                continue

            session = inst.get("session") if isinstance(inst, dict) else None
            if isinstance(session, dict):
                sf = str(session.get("file") or "").strip()
                session_file = sf or None

            msgs = inst.get("messages")
            if isinstance(msgs, dict):
                t = self._clean_message_text(str(msgs.get("lastAssistantText") or ""))
                h = str(msgs.get("lastAssistantHtml") or "").strip()
                if t:
                    latest_full = t
                elif h:
                    latest_full = self._html_to_text(h)
                if h:
                    latest_html = h
                latest_at = self._extract_timestamp_ms(msgs)
            break

        if not latest_full and session_file:
            latest_full, ts = self._latest_assistant_message(session_file)
            if ts is not None:
                latest_at = ts

        if not latest_full:
            tty = row.get("tty") or "??"
            mux, mux_session = self._infer_mux(row, by_pid)
            latest_full = self._latest_runtime_preview(pid, mux, mux_session, tty)

        if not latest_html:
            latest_html = self._message_html(latest_full)

        latest_gist = self._message_gist(latest_full)

        return {
            "ok": True,
            "pid": pid,
            "session_file": session_file,
            "latest_message": latest_gist,
            "latest_message_full": latest_full,
            "latest_message_html": latest_html,
            "latest_message_at": latest_at,
        }

    def send_message(self, pid: int, message: str) -> Dict:
        text = (message or "").strip()
        if not text:
            return {"ok": False, "error": "message is empty"}

        rows = self._ps_rows()
        by_pid = {r["pid"]: r for r in rows}
        row = next((r for r in rows if r["pid"] == pid and r["comm"] == "pi"), None)
        if row is None:
            return {"ok": False, "error": f"pi pid not found: {pid}"}

        tty = row.get("tty")
        mux, mux_session = self._infer_mux(row, by_pid)

        telemetry = self._telemetry_instance_for_pid(pid)
        routing = telemetry.get("routing") if isinstance(telemetry, dict) else None
        if isinstance(routing, dict):
            rmux = routing.get("mux")
            rsession = routing.get("muxSession")
            if isinstance(rmux, str) and rmux in ("zellij", "tmux", "screen"):
                mux = rmux
            if isinstance(rsession, str) and rsession.strip():
                mux_session = rsession.strip()

        # Prefer deterministic mux injection.
        if mux == "zellij" and mux_session:
            try:
                proc = subprocess.run(
                    ["zellij", "--session", mux_session, "action", "write-chars", text],
                    capture_output=True,
                    text=True,
                    timeout=1.2,
                )
                if proc.returncode == 0:
                    subprocess.run(
                        ["zellij", "--session", mux_session, "action", "write", "13"],
                        capture_output=True,
                        text=True,
                        timeout=1.2,
                    )
                    return {"ok": True, "pid": pid, "delivery": "zellij", "mux_session": mux_session}
            except Exception:
                pass

        if mux == "tmux":
            tmux_target = None
            if isinstance(routing, dict):
                tmux_info = routing.get("tmux")
                if isinstance(tmux_info, dict):
                    pane_target = tmux_info.get("paneTarget")
                    if isinstance(pane_target, str) and pane_target.strip():
                        tmux_target = pane_target.strip()
            if not tmux_target:
                tmux_target = self._tmux_target_for_tty(tty)

            delivered = self._send_tmux_message(text, mux_session, tmux_target)
            if delivered:
                return {
                    "ok": True,
                    "pid": pid,
                    "delivery": "tmux",
                    "mux_session": mux_session,
                    "tmux_target": tmux_target,
                }

        bridge_result = self._send_via_bridge(pid, text, mode="queued")
        if bridge_result is not None:
            if bridge_result.get("ok"):
                return bridge_result

            bridge_error = str(bridge_result.get("bridge_error") or "")
            bridge_msg = str(bridge_result.get("error") or "")
            bridge_rate_limited = bridge_error in ("rate_limited", "bridge_rate_limited", "pi_rate_limited") or ("rate_limited" in bridge_msg)

            # If bridge explicitly rate-limited this message, fall back to terminal delivery
            # so the user can keep working. Other bridge failures still fail fast to avoid
            # accidental duplicate sends when delivery state is unknown.
            if not bridge_rate_limited:
                return bridge_result

        # For zellij/screen, avoid raw TTY injection if mux routing exists but delivery failed.
        if mux in ("zellij", "screen"):
            return {
                "ok": False,
                "error": "could not deliver message via mux",
                "pid": pid,
                "mux": mux,
                "mux_session": mux_session,
                "tty": tty,
            }

        cwd = row.get("cwd")
        terminal_app, terminal_pid = self._detect_terminal_target_for_pid(pid, by_pid)
        hints = self._build_focus_hints(mux_session, cwd, tty)

        # Preferred fallback for iTerm2/Terminal: script-based write to the matching tty session/tab.
        if tty and tty != "??" and self._send_via_terminal_script(text, tty, terminal_app):
            return {
                "ok": True,
                "pid": pid,
                "delivery": "terminal-script",
                "tty": tty,
                "terminal_app": terminal_app,
            }

        # Last-resort for direct shell/tmux fallback: inject into tty input queue.
        if tty and tty != "??" and self._inject_tty_input(tty, text):
            return {"ok": True, "pid": pid, "delivery": "tty-input", "tty": tty}

        # If kernel tty injection is blocked, try UI typing on the matched terminal window.
        if self._send_via_ui_typing(text, terminal_app, hints, terminal_pid, tty):
            return {
                "ok": True,
                "pid": pid,
                "delivery": "ui-keystroke",
                "tty": tty,
                "terminal_app": terminal_app,
            }

        return {
            "ok": False,
            "error": "could not deliver message (mux, tty-input and ui-keystroke all failed)",
            "pid": pid,
            "mux": mux,
            "mux_session": mux_session,
            "tty": tty,
            "terminal_app": terminal_app,
        }

    def _latest_assistant_message(self, session_file: str | None) -> tuple[str | None, int | None]:
        if not session_file:
            return None, None

        path = Path(session_file)
        if not path.exists() or not path.is_file():
            return None, None

        try:
            stat = path.stat()
            key = str(path)
            cached = self._session_message_cache.get(key)
            if cached and cached.get("mtime") == stat.st_mtime_ns and cached.get("size") == stat.st_size:
                return cached.get("text"), cached.get("ts")

            with open(path, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - 512 * 1024), os.SEEK_SET)
                chunk = f.read().decode("utf-8", errors="ignore")
        except Exception:
            return None, None

        chunks: List[str] = []
        latest_ts: int | None = None
        started = False
        fallback_text: str | None = None
        fallback_ts: int | None = None

        for line in reversed(chunk.splitlines()):
            line = line.strip()
            if not line:
                continue

            try:
                obj = json.loads(line)
            except Exception:
                if started:
                    break
                continue

            is_assistant = self._is_assistant_message_obj(obj)
            if not is_assistant:
                if started and self._is_user_message_obj(obj):
                    break
                if started:
                    continue
                continue

            text = self._extract_text(obj)
            if not text:
                continue

            text = self._clean_message_text(text)
            if not text:
                continue

            ts = self._extract_timestamp_ms(obj)
            if latest_ts is None and ts is not None:
                latest_ts = ts

            if self._looks_like_tool_trace(text):
                continue

            if self._looks_like_thinking_or_status(text):
                if fallback_text is None:
                    fallback_text = text
                    fallback_ts = ts
                continue

            started = True
            chunks.append(text)

        if chunks:
            chunks.reverse()
            merged = self._merge_message_chunks(chunks)
            if len(merged) > 12000:
                merged = merged[:11997] + "..."
            self._session_message_cache[str(path)] = {
                "mtime": stat.st_mtime_ns,
                "size": stat.st_size,
                "text": merged,
                "ts": latest_ts,
            }
            return merged, latest_ts

        if fallback_text:
            fallback_text = self._strip_noise_lines(fallback_text)
            if len(fallback_text) > 8000:
                fallback_text = fallback_text[:7997] + "..."
            self._session_message_cache[str(path)] = {
                "mtime": stat.st_mtime_ns,
                "size": stat.st_size,
                "text": fallback_text,
                "ts": fallback_ts,
            }
            return fallback_text, fallback_ts

        self._session_message_cache[str(path)] = {
            "mtime": stat.st_mtime_ns,
            "size": stat.st_size,
            "text": None,
            "ts": None,
        }
        return None, None

    def _latest_runtime_preview(self, pid: int, mux: str | None, mux_session: str | None, tty: str | None) -> str | None:
        cached = self._runtime_preview_cache.get(pid)
        now = time.time()
        if cached and (now - float(cached.get("ts", 0))) < 4.0:
            return cached.get("msg")

        text: str | None = None

        if mux == "zellij" and mux_session:
            text = self._zellij_tail_preview(mux_session)
        elif mux == "tmux" and mux_session:
            text = self._tmux_tail_preview(mux_session)

        if not text and tty and tty != "??":
            text = f"waiting on {tty}"

        self._runtime_preview_cache[pid] = {"ts": now, "msg": text}
        return text

    def _zellij_tail_preview(self, mux_session: str) -> str | None:
        try:
            with tempfile.NamedTemporaryFile(prefix="statusd-zellij-", suffix=".txt", delete=False) as tmp:
                tmp_path = tmp.name
        except Exception:
            return None

        try:
            proc = subprocess.run(
                ["zellij", "--session", mux_session, "action", "dump-screen", "--full", tmp_path],
                capture_output=True,
                text=True,
                timeout=2.0,
            )
            if proc.returncode != 0:
                return None
            try:
                content = Path(tmp_path).read_text(errors="ignore")
            except Exception:
                return None
            return self._preview_from_terminal_dump(content)
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    def _tmux_tail_preview(self, mux_session: str) -> str | None:
        try:
            proc = subprocess.run(
                ["tmux", "-L", mux_session, "capture-pane", "-p", "-S", "-2000"],
                capture_output=True,
                text=True,
                timeout=2.0,
            )
            if proc.returncode != 0:
                return None
            return self._preview_from_terminal_dump(proc.stdout)
        except Exception:
            return None

    def _preview_from_terminal_dump(self, content: str | None) -> str | None:
        if not content:
            return None

        raw_lines = [self._clean_message_text(ln) for ln in content.splitlines()]
        lines = [ln for ln in raw_lines if ln]
        if not lines:
            return None

        selected: List[str] = []
        for line in reversed(lines):
            low = line.lower()
            if low.startswith(("$", "%", "❯", "➜", "~", "pi>")):
                continue
            if re.fullmatch(r"[-─═_\s>]+", line):
                continue
            if "statusd" in low and "blocked" in low:
                continue
            selected.append(line)
            if len(selected) >= 220:
                break

        if not selected:
            return None

        selected.reverse()
        text = "\n".join(selected).strip()
        if len(text) > 12000:
            text = text[:11997] + "..."
        return text

    def _clean_message_text(self, text: str | None) -> str:
        if not text:
            return ""

        # Remove ANSI escape sequences.
        out = re.sub(r"\x1B\[[0-?]*[ -/]*[@-~]", "", text)

        # Remove private-use and control chars that render as broken icon glyphs.
        cleaned_chars: List[str] = []
        for ch in out:
            cat = unicodedata.category(ch)
            if cat == "Co":
                continue
            if cat == "Cc" and ch not in ("\n", "\t"):
                continue
            cleaned_chars.append(ch)

        out = "".join(cleaned_chars)

        # Normalize spacing but keep line breaks for expanded view.
        lines = [ln.rstrip() for ln in out.splitlines()]
        out = "\n".join(lines).strip()

        # Collapse too many blank lines.
        out = re.sub(r"\n{3,}", "\n\n", out)
        return out

    def _merge_message_chunks(self, chunks: List[str]) -> str:
        merged: List[str] = []
        for chunk in chunks:
            c = chunk.strip()
            if not c:
                continue
            if merged and merged[-1] == c:
                continue
            # Handle cumulative streaming chunks where newer chunk extends older text.
            if merged and len(c) > len(merged[-1]) and c.startswith(merged[-1]):
                merged[-1] = c
                continue
            merged.append(c)
        out = "\n".join(merged).strip()
        return self._strip_noise_lines(out)

    def _strip_noise_lines(self, text: str) -> str:
        lines: List[str] = []
        for ln in text.splitlines():
            low = ln.lower().strip()
            if not low:
                lines.append(ln)
                continue
            if "/var/folders/" in low and "screenshot" in low:
                continue
            if "daemon/pi-statusbar" in low or "swift run pistatusbar" in low:
                continue
            if low.startswith(("edit ", "write ", "read ", "bash ", "rg ", "find ", "python3 ")):
                continue
            if "processes:" in low and "pi-statusbar-app" in low:
                continue
            if "visual latest" in low:
                continue
            lines.append(ln)
        out = "\n".join(lines).strip()
        out = re.sub(r"\n{3,}", "\n\n", out)
        return out

    def _message_gist(self, text: str | None) -> str | None:
        if not text:
            return None
        compact = " ".join(text.split())
        if len(compact) <= 420:
            return compact
        return "..." + compact[-417:]

    def _message_html(self, text: str | None) -> str | None:
        if not text:
            return None
        escaped = html_lib.escape(text)
        return f"<div class=\"pi-last-assistant\"><pre>{escaped}</pre></div>"

    def _html_to_text(self, html: str | None) -> str | None:
        if not html:
            return None
        raw = str(html).strip()
        if not raw:
            return None
        text = re.sub(r"<br\s*/?>", "\n", raw, flags=re.IGNORECASE)
        text = re.sub(r"</(p|div|li|h[1-6]|tr)>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        text = html_lib.unescape(text)
        cleaned = self._clean_message_text(text)
        return cleaned or None

    def _looks_like_tool_trace(self, text: str) -> bool:
        low = text.lower().strip()
        tool_markers = (
            "edit ", "write ", "read ", "bash ", "rg ", "find ", "python3 ",
            "daemon/pi-statusbar", "swift build", "processes:", "stderr:", "stdout:",
            "recipient_name", "tool_uses", "json.tool", "command exited with code",
        )
        if any(marker in low for marker in tool_markers):
            return True
        if low.startswith(("{" , "[")) and ("recipient_name" in low or "parameters" in low):
            return True
        return False

    def _looks_like_thinking_or_status(self, text: str) -> bool:
        low = text.lower()
        if "thinking" in low or "reasoning" in low:
            return True
        if "working..." in low or "visual latest" in low:
            return True
        if "processes:" in low and "pi-statusbar-app" in low:
            return True
        if "gpt-5" in low and "think:" in low:
            return True
        return False

    def _is_preview_line_candidate(self, line: str) -> bool:
        low = line.lower()
        if low.startswith(("$", "%", "❯", "➜", "~", "pi>")):
            return False
        if "statusd" in low and "blocked" in low:
            return False
        if "processes:" in low and "pi-statusbar-app" in low:
            return False
        if "visual latest" in low:
            return False
        if "gpt-5" in low and "think:" in low:
            return False
        if "pkgs" in low and "visual:" in low:
            return False

        if re.fullmatch(r"[-─═_\s>]+", line):
            return False

        alpha_count = sum(1 for ch in line if ch.isalpha())
        digit_count = sum(1 for ch in line if ch.isdigit())
        punctuation_count = sum(1 for ch in line if not ch.isalnum() and not ch.isspace())
        total = max(1, len(line))

        if (alpha_count + digit_count) < 8:
            return False

        if (punctuation_count / total) > 0.40:
            return False

        return True

    def _message_payload(self, obj: Dict) -> Dict:
        if str(obj.get("type") or "").lower() == "message" and isinstance(obj.get("message"), dict):
            return obj.get("message") or {}
        return obj

    def _is_user_message_obj(self, obj: Dict) -> bool:
        payload = self._message_payload(obj)
        role = str(payload.get("role") or "").lower()
        return role == "user"

    def _is_assistant_message_obj(self, obj: Dict) -> bool:
        payload = self._message_payload(obj)
        role = str(payload.get("role") or "").lower()
        if role in ("tool", "toolresult", "tool_result", "system", "user"):
            return False
        return role in ("assistant", "agent", "model")

    def _extract_text(self, obj: object) -> str | None:
        if isinstance(obj, str):
            s = obj.strip()
            return s or None

        if isinstance(obj, list):
            parts: List[str] = []
            for item in obj:
                t = self._extract_text(item)
                if t:
                    parts.append(t)
            if not parts:
                return None
            return "\n".join(parts)

        if not isinstance(obj, dict):
            return None

        payload = self._message_payload(obj)

        obj_type = str(payload.get("type") or "").lower()
        obj_role = str(payload.get("role") or "").lower()
        if obj_type in ("reasoning", "thinking", "analysis", "toolcall", "tool_call", "toolresult", "tool_result"):
            return None
        if obj_role in ("reasoning", "thinking", "tool", "toolresult", "tool_result"):
            return None

        content = payload.get("content")
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, dict):
                    item_type = str(item.get("type") or "").lower()
                    if item_type in (
                        "reasoning", "thinking", "analysis", "input_text", "input", "user",
                        "toolcall", "tool_call", "toolresult", "tool_result", "summary", "summary_text",
                    ):
                        continue
                    if item_type in ("text", "output_text"):
                        t = self._extract_text(item.get("text"))
                    else:
                        t = self._extract_text(item.get("content") or item.get("text") or item.get("output"))
                    if t:
                        parts.append(t)
                else:
                    t = self._extract_text(item)
                    if t:
                        parts.append(t)
            if parts:
                return "\n".join(parts)

        for key in ("text", "output"):
            if key in payload:
                t = self._extract_text(payload.get(key))
                if t:
                    return t

        return None

    def _extract_timestamp_ms(self, obj: Dict) -> int | None:
        def norm(value: object) -> int | None:
            if isinstance(value, (int, float)):
                n = int(value)
                if n > 1_000_000_000_000:
                    return n
                if n > 1_000_000_000:
                    return n * 1000
                return None
            if isinstance(value, str):
                s = value.strip()
                if not s:
                    return None
                try:
                    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
                    return int(dt.timestamp() * 1000)
                except Exception:
                    return None
            return None

        for key in ("timestamp", "ts", "createdAt", "updatedAt", "lastAssistantUpdatedAt"):
            out = norm(obj.get(key))
            if out is not None:
                return out

        payload = self._message_payload(obj)
        for key in ("timestamp", "ts", "createdAt", "updatedAt", "lastAssistantUpdatedAt"):
            out = norm(payload.get(key))
            if out is not None:
                return out

        data = obj.get("data")
        if isinstance(data, dict):
            for key in ("timestamp", "ts", "createdAt", "updatedAt", "lastAssistantUpdatedAt"):
                out = norm(data.get(key))
                if out is not None:
                    return out
        return None

    def jump(self, pid: int) -> Dict:
        rows = self._ps_rows()
        by_pid = {r["pid"]: r for r in rows}
        row = next((r for r in rows if r["pid"] == pid and r["comm"] == "pi"), None)
        if row is None:
            return {"ok": False, "error": f"pi pid not found: {pid}"}

        cwd = self._cwd_map([pid]).get(pid)
        tty = row["tty"]
        mux, mux_session = self._infer_mux(row, by_pid)

        focused = False
        focused_app = None
        focused_app_pid = None

        # 1) Prefer focusing via attached client when available.
        # This is most deterministic across multiple Ghostty instances/spaces.
        client_pid = self._find_mux_client_pid(mux, mux_session, tty, rows)
        client_tty = by_pid.get(client_pid, {}).get("tty") if client_pid else None
        focus_hints = self._build_focus_hints(
            mux_session=mux_session,
            cwd=cwd,
            tty=tty,
            client_tty=client_tty,
        )

        if client_pid:
            app, app_pid = self._detect_terminal_target_for_pid(client_pid, by_pid)
            if app:
                focused_app, focused_app_pid = app, app_pid
                focused = self._focus_terminal_app(focused_app, focus_hints, focused_app_pid)

        # 2) Fallback: focus terminal app from the pi process ancestry.
        if not focused:
            app, app_pid = self._detect_terminal_target_for_pid(pid, by_pid)
            if app:
                focused_app, focused_app_pid = app, app_pid
                focused = self._focus_terminal_app(focused_app, focus_hints, focused_app_pid)

        # 3) Ghostty global hint fallback (split panes can break PID ancestry).
        if not focused and focus_hints:
            focused = self._focus_ghostty_window_by_hints_any(focus_hints)
            if focused:
                focused_app = "Ghostty"

        # 4) TTY-based focus for iTerm2/Terminal
        if not focused and tty and tty != "??":
            focused = self._focus_terminal_by_tty(tty)

        # 5) title-hint focus (iTerm2/Terminal)
        if not focused and mux_session:
            focused = self._focus_terminal_by_title_hint(mux_session)
            if not focused and mux_session.startswith("agent-"):
                focused = self._focus_terminal_by_title_hint(mux_session[len("agent-"):])

        # 6) Ghostty fallback without launching new windows:
        # bring existing Ghostty process frontmost when client exists but window matching failed.
        if not focused and client_pid and focused_app == "Ghostty":
            focused = self._activate_existing_app("Ghostty")

        # 7) if no corresponding client is running, open a new shell and attach/open there
        opened_attach = False
        opened_shell = False
        if not focused and not client_pid:
            if mux == "zellij" and mux_session:
                opened_attach = self._open_terminal_with_shell(command=f"zellij attach {self._sh_quote(mux_session)}", cwd=cwd)
            elif cwd:
                opened_shell = self._open_terminal_with_shell(command=None, cwd=cwd)

        return {
            "ok": True,
            "pid": pid,
            "tty": tty,
            "cwd": cwd,
            "mux": mux,
            "mux_session": mux_session,
            "client_pid": client_pid,
            "focused": focused,
            "focused_app": focused_app,
            "focused_app_pid": focused_app_pid,
            "opened_attach": opened_attach,
            "opened_shell": opened_shell,
            "fallback_opened": False,
        }

    def _ps_rows(self) -> List[Dict]:
        cmd = ["/bin/ps", "-axo", "pid=,ppid=,comm=,state=,tty=,pcpu=,args="]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            return []

        rows: List[Dict] = []
        for raw in proc.stdout.splitlines():
            line = raw.strip()
            if not line:
                continue
            parts = line.split(None, 6)
            if len(parts) < 6:
                continue
            try:
                pid = int(parts[0])
                ppid = int(parts[1])
                comm = parts[2]
                state = parts[3]
                tty = parts[4]
                cpu = float(parts[5])
                args = parts[6] if len(parts) >= 7 else ""
            except ValueError:
                continue
            rows.append({
                "pid": pid,
                "ppid": ppid,
                "comm": comm,
                "state": state,
                "tty": tty,
                "cpu": cpu,
                "args": args,
            })
        return rows

    def _cwd_map(self, pids: List[int]) -> Dict[int, str]:
        out: Dict[int, str] = {}
        for pid in pids:
            try:
                proc = subprocess.run(
                    ["/usr/sbin/lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
                    capture_output=True,
                    text=True,
                    timeout=1.5,
                )
            except Exception:
                continue
            if proc.returncode != 0:
                continue
            for line in proc.stdout.splitlines():
                if line.startswith("n"):
                    out[pid] = line[1:]
                    break
        return out

    def _infer_activity(self, row: Dict) -> tuple[str, str]:
        state = row["state"]
        if state.startswith("R"):
            return "running", "high"
        if row["cpu"] >= 1.0:
            return "running", "medium"
        if state.startswith("S") and row["tty"] != "??":
            return "waiting_input", "medium"
        return "unknown", "low"

    def _infer_mux(self, row: Dict, by_pid: Dict[int, Dict]) -> tuple[str | None, str | None]:
        # Walk ancestors: pi is often launched from a shell whose parent is the mux server/client.
        seen = set()
        cur = row.get("ppid")
        hops = 0

        while cur and cur not in seen and hops < 20:
            seen.add(cur)
            hops += 1
            anc = by_pid.get(cur)
            if not anc:
                break

            args = anc.get("args", "")
            low = args.lower()
            if "zellij" in low:
                return "zellij", self._extract_zellij_session(args)
            if "tmux" in low:
                return "tmux", self._extract_tmux_session(args)
            if "screen" in low:
                return "screen", None

            cur = anc.get("ppid")

        return None, None

    def _extract_zellij_session(self, args: str) -> str | None:
        parts = args.split()
        for i, p in enumerate(parts):
            if p == "-s" and i + 1 < len(parts):
                return parts[i + 1]
            if p == "--session" and i + 1 < len(parts):
                return parts[i + 1]
            if p == "--server" and i + 1 < len(parts):
                return Path(parts[i + 1]).name
        return None

    def _extract_tmux_session(self, args: str) -> str | None:
        parts = args.split()
        for i, p in enumerate(parts):
            if p in ("-t", "--target") and i + 1 < len(parts):
                target = parts[i + 1].strip()
                if target:
                    return target.split(":", 1)[0]
            if p.startswith("-t") and len(p) > 2:
                target = p[2:].strip()
                if target:
                    return target.split(":", 1)[0]
        return None

    def _find_mux_client_pid(self, mux: str | None, mux_session: str | None, tty: str | None, rows: List[Dict]) -> int | None:
        if not mux:
            return None

        # Prefer explicit client command lines (not mux server).
        if mux_session:
            for r in rows:
                args = r.get("args", "")
                if mux == "zellij" and "zellij" in args and "--server" not in args and mux_session in args:
                    return r["pid"]
                if mux == "tmux" and "tmux" in args and mux_session in args:
                    return r["pid"]
                if mux == "screen" and "screen" in args and mux_session in args:
                    return r["pid"]

        # Fallback: same TTY client process.
        if tty and tty != "??":
            for r in rows:
                args = (r.get("args") or "")
                if r.get("tty") != tty:
                    continue
                if mux == "zellij" and "zellij" in args and "--server" not in args:
                    return r["pid"]
                if mux == "tmux" and "tmux" in args:
                    return r["pid"]
                if mux == "screen" and "screen" in args:
                    return r["pid"]

        return None

    def _detect_terminal_target_for_pid(self, pid: int, by_pid: Dict[int, Dict]) -> tuple[str | None, int | None]:
        seen = set()
        cur = pid
        while cur and cur not in seen:
            seen.add(cur)
            row = by_pid.get(cur)
            if not row:
                break

            comm = (row.get("comm") or "").lower()
            args = (row.get("args") or "").lower()

            if comm in ("ghostty",) or "ghostty" in comm or "ghostty" in args:
                return "Ghostty", cur
            if comm in ("iterm2", "iterm") or "iterm" in comm or "iterm" in args:
                return "iTerm2", cur
            if comm in ("terminal",) or "terminal" in comm or "terminal.app/contents/macos/terminal" in args:
                return "Terminal", cur

            cur = row.get("ppid")

        return None, None

    def _build_focus_hints(
        self,
        mux_session: str | None,
        cwd: str | None,
        tty: str | None,
        client_tty: str | None = None,
    ) -> List[str]:
        hints: List[str] = []
        if mux_session:
            hints.append(mux_session)
            if mux_session.startswith("agent-"):
                hints.append(mux_session[len("agent-"):])
        if cwd:
            hints.append(Path(cwd).name)
        if tty and tty != "??":
            hints.append(tty)
        if client_tty and client_tty != "??":
            hints.append(client_tty)
        # Preserve order but deduplicate.
        out: List[str] = []
        seen = set()
        for h in hints:
            key = h.lower()
            if key not in seen:
                seen.add(key)
                out.append(h)
        return out

    def _focus_terminal_app(self, app_name: str, hints: List[str], app_pid: int | None = None) -> bool:
        if app_name == "Ghostty":
            if not hints or not app_pid:
                return False
            # Try to raise the exact Ghostty window (strict matching to avoid wrong desktop jumps).
            if self._focus_ghostty_window_by_title_hints(hints, app_pid):
                return True
            # Avoid claiming success for Ghostty when we could not target the specific session window.
            return False

        return self._activate_app(app_name)

    def _focus_ghostty_window_by_title_hints(self, hints: List[str], app_pid: int) -> bool:
        cleaned = [h for h in hints if h]
        if not cleaned:
            return False

        hint_list = ", ".join(f'"{self._applescript_escape(h)}"' for h in cleaned)
        script = f'''
set needles to {{{hint_list}}}
set targetPid to {app_pid}
try
  tell application "System Events"
    set targetProcess to missing value
    try
      set targetProcess to first process whose unix id is targetPid
    end try
    if targetProcess is missing value then
      return "no"
    end if

    tell targetProcess
      repeat with w in windows
        try
          set n to (name of w as text)
          repeat with needle in needles
            ignoring case
              if n contains (needle as text) then
                tell application "Ghostty" to activate
                set frontmost to true
                perform action "AXRaise" of w
                return "ok"
              end if
            end ignoring
          end repeat
        end try
      end repeat
    end tell
  end tell
end try
return "no"
'''
        return self._run_osascript(script) == "ok"

    def _focus_ghostty_window_by_hints_any(self, hints: List[str]) -> bool:
        cleaned = [h for h in hints if h]
        if not cleaned:
            return False

        hint_list = ", ".join(f'"{self._applescript_escape(h)}"' for h in cleaned)
        script = f'''
set needles to {{{hint_list}}}
try
  tell application "System Events"
    if not (exists process "Ghostty") then
      return "no"
    end if
    tell process "Ghostty"
      repeat with w in windows
        try
          set n to (name of w as text)
          repeat with needle in needles
            ignoring case
              if n contains (needle as text) then
                tell application "Ghostty" to activate
                set frontmost to true
                perform action "AXRaise" of w
                return "ok"
              end if
            end ignoring
          end repeat
        end try
      end repeat
    end tell
  end tell
end try
return "no"
'''
        return self._run_osascript(script) == "ok"

    def _activate_existing_app(self, app_name: str) -> bool:
        app = self._applescript_escape(app_name)
        script = f'''
try
  tell application "System Events"
    if exists process "{app}" then
      tell process "{app}"
        set frontmost to true
        try
          if (count of windows) > 0 then
            perform action "AXRaise" of window 1
          end if
        end try
      end tell
      return "ok"
    end if
  end tell
end try
return "no"
'''
        return self._run_osascript(script) == "ok"

    def _activate_app(self, app_name: str) -> bool:
        app = self._applescript_escape(app_name)
        script = f'''
try
  tell application "{app}" to activate
  delay 0.05

  -- Stronger focus path for Space/Desktop setups:
  -- make process frontmost and try AXRaise on front window.
  tell application "System Events"
    if exists process "{app}" then
      tell process "{app}"
        set frontmost to true
        try
          if (count of windows) > 0 then
            perform action "AXRaise" of window 1
          end if
        end try
      end tell
    end if
  end tell

  return "ok"
end try
return "no"
'''
        return self._run_osascript(script) == "ok"

    def _summarize(self, agents: List[Agent]) -> Dict:
        total = len(agents)
        running = sum(1 for a in agents if a.activity == "running")
        waiting = sum(1 for a in agents if a.activity == "waiting_input")
        unknown = total - running - waiting

        if total == 0:
            color, label = "gray", "No Pi agents"
        elif waiting == 0 and unknown == 0:
            color, label = "red", "All agents running"
        elif waiting == total and unknown == 0:
            color, label = "green", "All agents waiting for input"
        else:
            color, label = "yellow", "Some agents waiting for input"

        return {
            "total": total,
            "running": running,
            "waiting_input": waiting,
            "unknown": unknown,
            "color": color,
            "label": label,
        }

    def _focus_terminal_by_tty(self, tty: str) -> bool:
        t = self._applescript_escape(tty)
        iterm_script = f'''
set targetTTY to "{t}"
try
  tell application "iTerm2"
    repeat with w in windows
      repeat with tb in tabs of w
        repeat with s in sessions of tb
          try
            if (tty of s as text) ends with targetTTY then
              tell w to select tb
              activate
              return "ok"
            end if
          end try
        end repeat
      end repeat
    end repeat
  end tell
end try
return "no"
'''
        if self._run_osascript(iterm_script) == "ok":
            return True

        terminal_script = f'''
set targetTTY to "{t}"
try
  tell application "Terminal"
    repeat with w in windows
      repeat with tb in tabs of w
        try
          if (tty of tb as text) ends with targetTTY then
            set selected of tb to true
            activate
            return "ok"
          end if
        end try
      end repeat
    end repeat
  end tell
end try
return "no"
'''
        return self._run_osascript(terminal_script) == "ok"

    def _focus_terminal_by_title_hint(self, hint: str) -> bool:
        h = self._applescript_escape(hint)
        script = f'''
set needle to "{h}"
try
  tell application "iTerm2"
    repeat with w in windows
      repeat with tb in tabs of w
        try
          if (name of tb as text) contains needle then
            tell w to select tb
            activate
            return "ok"
          end if
        end try
      end repeat
    end repeat
  end tell
end try
try
  tell application "Terminal"
    repeat with w in windows
      repeat with tb in tabs of w
        try
          if (custom title of tb as text) contains needle then
            set selected of tb to true
            activate
            return "ok"
          end if
        end try
      end repeat
    end repeat
  end tell
end try
return "no"
'''
        return self._run_osascript(script) == "ok"

    def _default_shell(self) -> str:
        shell = os.environ.get("SHELL", "").strip()
        if shell:
            return shell
        try:
            return pwd.getpwuid(os.getuid()).pw_shell or "/bin/zsh"
        except Exception:
            return "/bin/zsh"

    def _load_config(self) -> Dict:
        try:
            if CONFIG_PATH.exists():
                return json.loads(CONFIG_PATH.read_text())
        except Exception:
            pass
        return {}

    def _configured_terminal(self) -> str | None:
        env_val = os.environ.get("PI_STATUS_TERMINAL", "").strip()
        cfg = self._load_config()
        raw = env_val or str(cfg.get("terminal") or cfg.get("preferred_terminal") or "").strip()
        if not raw:
            return None

        low = raw.lower()
        if low in ("ghostty",):
            return "Ghostty"
        if low in ("iterm2", "iterm", "iterm.app"):
            return "iTerm2"
        if low in ("terminal", "terminal.app", "apple_terminal"):
            return "Terminal"
        if low in ("auto", "system", "default"):
            return None
        return None

    def _app_available(self, app_name: str) -> bool:
        bundle = {
            "Ghostty": "Ghostty.app",
            "iTerm2": "iTerm.app",
            "Terminal": "Terminal.app",
        }.get(app_name, f"{app_name}.app")
        proc = subprocess.run(["/usr/bin/open", "-Ra", bundle], capture_output=True, text=True)
        return proc.returncode == 0

    def _resolve_terminal_app(self) -> str:
        configured = self._configured_terminal()
        if configured and self._app_available(configured):
            return configured

        # Default preference order.
        for app in ("Ghostty", "iTerm2", "Terminal"):
            if self._app_available(app):
                return app

        return "Terminal"

    def _open_terminal_with_shell(self, command: str | None, cwd: str | None) -> bool:
        shell = self._default_shell()
        parts: List[str] = []
        if cwd:
            parts.append(f"cd {self._sh_quote(cwd)}")

        if command:
            parts.append(f"exec {self._sh_quote(shell)} -lc {self._sh_quote(command)}")
        else:
            parts.append(f"exec {self._sh_quote(shell)} -l")

        launch_cmd = "; ".join(parts)
        app = self._resolve_terminal_app()

        if app == "Ghostty":
            proc = subprocess.run(
                ["/usr/bin/open", "-na", "Ghostty.app", "--args", "-e", shell, "-lc", launch_cmd],
                capture_output=True,
                text=True,
            )
            return proc.returncode == 0

        cmd = self._applescript_escape(launch_cmd)
        if app == "iTerm2":
            script = f'''
try
  tell application "iTerm2"
    activate
    create window with default profile command "{cmd}"
    return "ok"
  end tell
end try
return "no"
'''
            return self._run_osascript(script) == "ok"

        script = f'''
try
  tell application "Terminal"
    activate
    do script "{cmd}"
    return "ok"
  end tell
end try
return "no"
'''
        return self._run_osascript(script) == "ok"

    def _run_osascript(self, script: str) -> str:
        proc = subprocess.run(["/usr/bin/osascript", "-e", script], capture_output=True, text=True)
        if proc.returncode != 0:
            err = (proc.stderr or "").strip()
            if err:
                print(f"[statusd] osascript error: {err}", flush=True)
            return "err"
        return proc.stdout.strip().lower() or "no"

    def _applescript_escape(self, s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')

    def _sh_quote(self, s: str) -> str:
        return "'" + s.replace("'", "'\\''") + "'"

    def _to_int(self, value: object, default: int | None = None) -> int | None:
        try:
            if isinstance(value, bool):
                return default
            parsed = int(value)  # type: ignore[arg-type]
            return parsed
        except Exception:
            return default


def parse_request(req: str, scanner: Scanner) -> Dict:
    req = req.strip()
    if req == "" or req == "status":
        return scanner.scan()
    if req == "ping":
        return {"ok": True, "pong": True, "timestamp": int(time.time())}
    if req.startswith("jump "):
        _, pid_s = req.split(" ", 1)
        try:
            return scanner.jump(int(pid_s.strip()))
        except ValueError:
            return {"ok": False, "error": f"invalid pid: {pid_s}"}
    if req.startswith("latest "):
        _, pid_s = req.split(" ", 1)
        try:
            return scanner.latest_message(int(pid_s.strip()))
        except ValueError:
            return {"ok": False, "error": f"invalid pid: {pid_s}"}
    if req.startswith("send "):
        parts = req.split(" ", 2)
        if len(parts) < 3:
            return {"ok": False, "error": "usage: send <pid> <message>"}
        _, pid_s, message = parts
        try:
            return scanner.send_message(int(pid_s.strip()), message)
        except ValueError:
            return {"ok": False, "error": f"invalid pid: {pid_s}"}
    if req.startswith("watch"):
        parts = req.split()
        timeout_ms = 20_000
        since = ""
        if len(parts) >= 2:
            try:
                timeout_ms = max(250, min(60_000, int(parts[1])))
            except ValueError:
                timeout_ms = 20_000
        if len(parts) >= 3:
            since = parts[2]

        deadline = time.time() + (timeout_ms / 1000.0)
        while True:
            status = scanner.scan()
            fingerprint = _status_fingerprint(status)
            if fingerprint != since or time.time() >= deadline:
                return {
                    "ok": True,
                    "event": "status_changed" if fingerprint != since else "timeout",
                    "fingerprint": fingerprint,
                    "status": status,
                }
            time.sleep(0.4)
    return {"ok": False, "error": f"unknown request: {req}"}


def _status_fingerprint(status: Dict) -> str:
    agents = status.get("agents") if isinstance(status, dict) else None
    if not isinstance(agents, list):
        agents = []

    slim = []
    for item in agents:
        if not isinstance(item, dict):
            continue
        slim.append({
            "pid": item.get("pid"),
            "activity": item.get("activity"),
            "latest_message": item.get("latest_message"),
            "latest_message_at": item.get("latest_message_at"),
        })
    slim.sort(key=lambda x: (x.get("pid") or 0))
    return json.dumps(slim, sort_keys=True, separators=(",", ":"))


def handle_client(conn: socket.socket, scanner: Scanner) -> None:
    try:
        data = conn.recv(4096)
        req = data.decode("utf-8", errors="ignore")
        resp = parse_request(req, scanner)
        try:
            conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))
        except BrokenPipeError:
            # Client closed early; keep daemon alive.
            pass
    except Exception:
        # Never let one bad client crash the daemon loop.
        pass
    finally:
        conn.close()


def request(req: str) -> Dict:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(str(SOCKET_PATH))
    s.sendall((req.strip() + "\n").encode("utf-8"))

    chunks: List[bytes] = []
    while True:
        data = s.recv(65535)
        if not data:
            break
        chunks.append(data)
        if b"\n" in data:
            break

    s.close()
    payload = b"".join(chunks).decode("utf-8", errors="ignore").strip()
    return json.loads(payload)


def run_server() -> None:
    scanner = Scanner()
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    if SOCKET_PATH.exists():
        SOCKET_PATH.unlink()

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(SOCKET_PATH))
    os.chmod(SOCKET_PATH, 0o600)
    server.listen(32)

    try:
        while True:
            conn, _ = server.accept()
            handle_client(conn, scanner)
    finally:
        server.close()
        if SOCKET_PATH.exists():
            SOCKET_PATH.unlink()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--request", type=str)
    args = parser.parse_args()

    scanner = Scanner()
    if args.once:
        print(json.dumps(scanner.scan()))
        return
    if args.request:
        print(json.dumps(request(args.request)))
        return
    run_server()


if __name__ == "__main__":
    main()
