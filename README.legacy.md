# Pi Status (daemon + macOS menu bar UI)

This project is now split into two parts:

1. **`pi-statusd` daemon** (source of truth)
2. **`PiStatusBar` SwiftUI menu bar app** (UI client)

## 1) Daemon: `daemon/pi_statusd.py`

Responsibilities:

- Detect all running `pi` processes for current user (works across shell/tmux/zellij/screen).
- Infer agent activity (`running` / `waiting_input` / `unknown`).
- Compute aggregate color state:
  - green = all running
  - yellow = mixed
  - red = all waiting
  - gray = none
- Expose local Unix socket API at:
  - `~/.pi/agent/statusd.sock`

Security:

- No network port; local Unix socket only.
- Socket permissions are `0600`.

### Daemon API

Send one line per request:

- `status` → full JSON snapshot
- `ping` → health response
- `jump <pid>` → best-effort focus/open for that agent

Examples:

```bash
python3 daemon/pi_statusd.py --request status
python3 daemon/pi_statusd.py --request "jump 57017"
```

Run daemon:

```bash
python3 daemon/pi_statusd.py
```

Or use the helper (recommended):

```bash
daemon/statusdctl start
daemon/statusdctl status
daemon/statusdctl restart
daemon/statusdctl stop
```

Logs are written to `~/.pi/agent/statusd.log`.

Configure terminal used for "open new shell/attach" fallback:

```bash
daemon/statusdctl terminal           # show current setting
daemon/statusdctl terminal Ghostty   # or: iTerm2, Terminal, auto
```

This is stored in `~/.pi/agent/statusd.json` (`terminal` key).

## 2) macOS UI: `PiStatusBar`

Responsibilities:

- Poll daemon every 2s via Unix socket.
- Render π icon with aggregate color.
- List all agents with per-agent state.
- Clicking an agent sends `jump <pid>`.

Run UI:

```bash
swift run PiStatusBar
```

Or from built binary:

```bash
.build/arm64-apple-macosx/debug/PiStatusBar
```

## Jump behavior (current)

`jump <pid>` is best-effort:

- tries to focus the terminal app/window for that agent (including AXRaise)
- tries to focus matching iTerm2/Terminal tab by TTY
- only when no corresponding client is running:
  - for zellij sessions: opens Terminal and runs `zellij attach <session>` using your default login shell
  - otherwise: opens Terminal in the agent cwd using your default login shell

Note for multiple Spaces/desktops: macOS controls cross-Space app switching via Mission Control settings.
For best results, enable:

- **System Settings → Desktop & Dock → Mission Control →**
  **“When switching to an application, switch to a Space with open windows for the application”**
