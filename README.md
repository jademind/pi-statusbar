# pi-statusbar

Home repository for the Pi desktop status stack:

1. **`pi-statusd`** daemon (process discovery, telemetry aggregation, jump/focus behavior)
2. **`PiStatusBar`** macOS menu bar client (SwiftUI)

This repo is intentionally separate from [`pi-telemetry`](https://github.com/jademind/pi-telemetry):

- `pi-telemetry` publishes runtime telemetry from inside Pi processes
- `pi-statusbar` consumes that telemetry for UX and orchestration

---

## Architecture

### Data source strategy

`pi-statusd` uses a layered approach:

1. **Primary:** `pi-telemetry-snapshot` (if available)
2. **Fallback:** process-tree heuristics via `ps`/`lsof`

This guarantees the status UI keeps working even when telemetry is not installed yet.

### Jump behavior

`jump <pid>` follows this order:

1. Focus attached mux client terminal window (preferred)
2. Fallback to focus via Pi process ancestry
3. Ghostty hint matching for split-pane scenarios
4. TTY/title matching for iTerm2/Terminal
5. If no attached client exists, open new shell/attach session
6. Never Finder fallback

---

## Components

### Daemon

- Path: `daemon/pi_statusd.py`
- Socket: `~/.pi/agent/statusd.sock`
- Commands:
  - `status`
  - `ping`
  - `jump <pid>`

### Control script

- Path: `daemon/statusdctl`
- Commands:
  - `start`, `stop`, `restart`, `status`, `ping`
  - `terminal [auto|Ghostty|iTerm2|Terminal]`

### macOS app

- Swift package product: `PiStatusBar`
- Path: `Sources/PiStatusBar/*`
- Poll interval: 2s
- Displays aggregate color + per-agent rows + Jump action

---

## Build and run

### Start daemon

```bash
daemon/statusdctl start
```

### Run status bar app

```bash
swift run PiStatusBar
```

---

## Telemetry integration

For best results, install telemetry package globally:

```bash
pi install npm:pi-telemetry
```

Then daemon status responses include telemetry-derived fields (including context pressure) whenever available.

---

## Development notes

- Socket permissions are local-user only (`0600`)
- Keep daemon and app restarted after code changes
- Legacy notes are kept in `README.legacy.md`

---

## License

MIT — see [LICENSE](./LICENSE).
