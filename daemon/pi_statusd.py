#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import time
import pwd
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List

SOCKET_PATH = Path.home() / ".pi" / "agent" / "statusd.sock"
CONFIG_PATH = Path.home() / ".pi" / "agent" / "statusd.json"


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
    telemetry_source: str | None = None
    context_percent: float | None = None
    context_pressure: str | None = None
    context_close_to_limit: bool | None = None
    context_near_limit: bool | None = None


class Scanner:
    def scan(self) -> Dict:
        rows = self._ps_rows()
        by_pid = {r["pid"]: r for r in rows}
        telemetry_instances = self._read_pi_telemetry_instances()

        if telemetry_instances:
            agents = self._agents_from_telemetry(telemetry_instances, rows, by_pid)
        else:
            agents = self._agents_from_processes(rows, by_pid)

        agents.sort(key=lambda a: a.pid)
        return {
            "ok": True,
            "timestamp": int(time.time()),
            "agents": [asdict(a) for a in agents],
            "summary": self._summarize(agents),
            "version": 2,
            "source": "pi-telemetry" if telemetry_instances else "process-fallback",
        }

    def _agents_from_processes(self, rows: List[Dict], by_pid: Dict[int, Dict]) -> List[Agent]:
        pi_rows = [r for r in rows if r["comm"] == "pi"]
        pids = [r["pid"] for r in pi_rows]
        cwd_map = self._cwd_map(pids)

        agents: List[Agent] = []
        for row in pi_rows:
            activity, confidence = self._infer_activity(row)
            mux, mux_session = self._infer_mux(row, by_pid)
            client_pid = self._find_mux_client_pid(mux, mux_session, row["tty"], rows)
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
                    telemetry_source=None,
                )
            )
        return agents

    def _agents_from_telemetry(self, telemetry_instances: List[Dict], rows: List[Dict], by_pid: Dict[int, Dict]) -> List[Agent]:
        pids = [int(i.get("process", {}).get("pid")) for i in telemetry_instances if i.get("process", {}).get("pid")]
        cwd_map = self._cwd_map(pids)
        agents: List[Agent] = []

        for instance in telemetry_instances:
            process = instance.get("process") or {}
            state_info = instance.get("state") or {}
            workspace = instance.get("workspace") or {}
            context = instance.get("context") or {}

            pid = int(process.get("pid", 0))
            if pid <= 0:
                continue

            row = by_pid.get(pid, {})
            tty = row.get("tty") or "??"
            mux, mux_session = self._infer_mux(row, by_pid) if row else (None, None)
            client_pid = self._find_mux_client_pid(mux, mux_session, tty, rows) if row else None

            agents.append(
                Agent(
                    pid=pid,
                    ppid=int(process.get("ppid") or row.get("ppid") or 0),
                    state=str(row.get("state") or "?"),
                    tty=str(tty),
                    cpu=float(row.get("cpu") or 0.0),
                    cwd=str(workspace.get("cwd") or cwd_map.get(pid) or "") or None,
                    activity=self._map_telemetry_activity(state_info.get("activity")),
                    confidence="high",
                    mux=mux,
                    mux_session=mux_session,
                    client_pid=client_pid,
                    telemetry_source=str(instance.get("source") or "pi-telemetry"),
                    context_percent=float(context.get("percent")) if isinstance(context.get("percent"), (int, float)) else None,
                    context_pressure=str(context.get("pressure")) if context.get("pressure") is not None else None,
                    context_close_to_limit=bool(context.get("closeToLimit")) if "closeToLimit" in context else None,
                    context_near_limit=bool(context.get("nearLimit")) if "nearLimit" in context else None,
                )
            )

        return agents

    def _read_pi_telemetry_instances(self) -> List[Dict]:
        commands = [["pi-telemetry-snapshot"]]

        for cmd in commands:
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1.2)
            except Exception:
                continue
            if proc.returncode != 0 or not proc.stdout.strip():
                continue
            try:
                payload = json.loads(proc.stdout)
            except Exception:
                continue
            instances = payload.get("instances")
            if isinstance(instances, list):
                return instances
        return []

    def _map_telemetry_activity(self, activity: object) -> str:
        if activity == "working":
            return "running"
        if activity == "waiting_input":
            return "waiting_input"
        return "unknown"

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
            if p in ("-L", "-S") and i + 1 < len(parts):
                return parts[i + 1]
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
    return {"ok": False, "error": f"unknown request: {req}"}


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
    data = s.recv(65535)
    s.close()
    return json.loads(data.decode("utf-8", errors="ignore"))


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
