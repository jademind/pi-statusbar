# Pi Status Bar

A **macOS status bar application for Pi** with a local daemon for process discovery, telemetry aggregation, and one-click session jump/focus.

This repository contains:

- **`pi-statusd`** (Python daemon): discovers Pi agents, merges telemetry, and handles jump/focus actions.
- **`PiStatusBar`** (SwiftUI app): menu bar UI that visualizes agent state and context pressure.

> `pi-statusbar` consumes telemetry from the separate [`pi-telemetry`](https://github.com/jademind/pi-telemetry) package.

## Version

Current version: **0.1.0**

---

## Privacy and sensitive data

- This README avoids machine-specific absolute paths and credentials.
- Runtime data is stored locally under `~/.pi/agent`.
- Do not commit local logs, telemetry dumps, or screenshots that expose private paths/session names.

## What it does

- Shows a live menu bar indicator of overall Pi agent state
- Lists active agents with:
  - mux/session + telemetry session name + activity
  - cwd path
  - pid + window attachment status (including detected terminal app)
  - model + token metrics (when telemetry is available)
  - context pressure line with clear health/mood signal
- Supports **Jump** to the correct terminal window/session
- Falls back gracefully when telemetry is unavailable

### State colors

- **Red**: all agents running/working
- **Green**: all agents waiting for input
- **Yellow**: mixed states
- **White/neutral**: no agents

---

## Architecture

### 1) Daemon (`daemon/pi_statusd.py`)

- Unix socket: `~/.pi/agent/statusd.sock`
- Commands:
  - `status`
  - `ping`
  - `jump <pid>`
- Status payload version: `2`
- Source field values:
  - `pi-telemetry`
  - `process-fallback`

### 2) macOS status bar app (`Sources/PiStatusBar/*`)

- Polls daemon every 2 seconds
- Renders status chips:
  - source (`telemetry | fallback | offline`)
  - active/running/waiting counts
  - context pressure (`close to limit` / `at limit`)
- Shows attention banners for close-to-limit/at-limit context pressure

### Agent row layout

Each agent row is rendered as:

1. Primary line: mux/session + telemetry session name (when available) + activity
2. Workspace line: cwd path
3. Attachment line: `PID <pid> · window attached|no attached window · <terminal app>`
4. Model metrics line: model name/id + token usage (`used/contextWindow`) when telemetry provides it
5. Context line: context percent + classification (`healthy`, `close to limit`, `at limit`) with emoji indicator

When telemetry is unavailable, the row gracefully falls back to process-only metadata.

---

## Telemetry compatibility (latest `pi-telemetry`)

`pi-statusd` follows this data source strategy:

1. **Primary:** per-process telemetry files at:
   - `~/.pi/agent/telemetry/instances/*.json`
2. **Optional fallback:** `pi-telemetry-snapshot`
3. **Final fallback:** process heuristics via `ps` + `lsof`

Compatibility notes:

- Supports telemetry activity mapping (`working`, `waiting_input`, fallback inference)
- Reads context metrics (`tokens`, `contextWindow`, `remainingTokens`, `percent`, `pressure`, `closeToLimit`, `nearLimit`)
- Reads model/session metadata for richer UI rows (`model.*`, `session.name`)
- Adds window attachment/app metadata in daemon responses (`attached_window`, `terminal_app`)
- Applies stale/alive filtering when reading telemetry files
- Defensively handles malformed telemetry entries

Install telemetry:

```bash
pi install npm:pi-telemetry
```

---

## Jump behavior

When `jump <pid>` is requested, the daemon uses this order:

1. Focus attached mux client window
2. Fallback focus via Pi ancestry
3. Ghostty hint fallback (split pane support)
4. TTY/title fallback for iTerm2/Terminal
5. If no attached client exists, open terminal + attach/open shell
6. Never Finder fallback

Terminal preference config:

- Config file: `~/.pi/agent/statusd.json`
- Env override: `PI_STATUS_TERMINAL`
- Values: `auto | Ghostty | iTerm2 | Terminal`
- Auto order: `Ghostty → iTerm2 → Terminal`

---

## Build and run

### Start / restart daemon

```bash
daemon/statusdctl restart
```

### Verify daemon health

```bash
daemon/statusdctl status
daemon/statusdctl ping
```

### Run macOS status bar app

```bash
swift run PiStatusBar
```

### Build

```bash
swift build
```

---

## Control script

`daemon/statusdctl` supports:

- `start`
- `stop`
- `restart`
- `status`
- `ping`
- `terminal`
- `terminal <auto|Ghostty|iTerm2|Terminal>`

---

## Operational guidance

- Keep daemon and app restarted after daemon/UI changes
- If UI shows fallback unexpectedly, verify active Pi sessions are emitting telemetry (`/pi-telemetry`)
- Socket is user-local (`0600` permissions)

---

## Troubleshooting

### App shows `daemon: offline`

```bash
daemon/statusdctl restart
daemon/statusdctl status
```

If status is unhealthy, inspect `~/.pi/agent/statusd.log`.

### Source chip stays on `fallback`

- Confirm telemetry is installed: `pi install npm:pi-telemetry`
- In an active Pi session run: `/pi-telemetry --data`
- Ensure telemetry files exist under `~/.pi/agent/telemetry/instances`

### Row shows `shell` without session name

- This is expected in process-fallback mode (no telemetry session metadata)
- With telemetry enabled, `session.name` is shown in the row primary line

### Jump does not focus expected window

- Ensure Accessibility permissions are granted for terminal apps and automation
- If using Ghostty split panes, keep session hints in titles/session names
- Verify terminal preference via:

```bash
daemon/statusdctl terminal
daemon/statusdctl terminal Ghostty
```

---

## License

MIT — see [LICENSE](./LICENSE).
