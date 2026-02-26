# Pi Statusbar HTTP API (v3)

This document describes the **current** HTTP bridge API exposed by `daemon/pi_statusd_http.py`.

> **Breaking change notice:** API v3 is **not backward compatible** with previous versions.
> - `/latest/{pid}` was removed.
> - `/watch` response format changed.
> - New per-agent watch endpoint: `/watch/{pid}` (long-poll + SSE).

---

## Base URLs

- HTTP: `http://<host>:<http_port>`
- HTTPS: `https://<host>:<https_port>`

Ports and token are configured via `~/.pi/agent/statusd-http.json` and controlled with:

```bash
pi-statusbar http-token
pi-statusbar http-status
```

---

## Authentication

Non-loopback requests require authentication.

Send either:

- `Authorization: Bearer <token>`
- `X-Statusd-Token: <token>`

Get/set token:

```bash
pi-statusbar http-token
pi-statusbar http-token <value>
```

---

## Response conventions

- JSON responses include `ok: true/false`.
- `timestamp` values are unix seconds unless explicitly `_at` fields (ms).
- Fingerprints are opaque strings for client diff/watch flows.

---

## Endpoints

## GET `/`
Service metadata.

### Response
```json
{
  "ok": true,
  "service": "pi-statusd-http",
  "api_version": 3
}
```

---

## GET `/health`
Basic health probe for the HTTP bridge.

### Response
```json
{
  "ok": true,
  "pong": true,
  "timestamp": 1772119999
}
```

---

## GET `/tls`
TLS runtime metadata.

### Response
```json
{
  "ok": true,
  "https_enabled": true,
  "https_port": 8788,
  "cert_sha256": "AA:BB:..."
}
```

---

## GET `/status`
Returns full daemon status with per-agent message metadata and top-level fingerprint.

### Response shape (abridged)
```json
{
  "ok": true,
  "timestamp": 1772119999,
  "version": 2,
  "source": "pi-telemetry",
  "summary": { "color": "yellow", "label": "Some agents waiting for input" },
  "fingerprint": "9f8e...",
  "agents": [
    {
      "pid": 12345,
      "activity": "running",
      "latest_message": "short preview",
      "latest_message_full": "full text",
      "latest_message_html": "<p>...</p>",
      "latest_message_at": 1772119000123,
      "latest_message_id": "3c2f4e..."
    }
  ]
}
```

### Notes
- `latest_message_id` is derived from `(pid, latest_message_at, latest_message[_full])`.
- Use `fingerprint` to drive long-polling via `/watch`.

---

## GET `/watch`
Global long-poll watch for status/message/activity changes.

### Query params
- `timeout_ms` (optional, default `20000`, range `250..60000`)
- `fingerprint` (optional)

### Behavior
- If `fingerprint` missing: immediate `snapshot`.
- If `fingerprint` differs from current state: immediate `out_of_sync`.
- If equal: waits until changed or timeout.

### Response events
- `snapshot`
- `out_of_sync`
- `status_changed`
- `timeout`

### `status_changed` payload example
```json
{
  "ok": true,
  "event": "status_changed",
  "fingerprint": "new_fp",
  "changes": [
    { "event": "activity_changed", "pid": 12345, "activity": "waiting_input" },
    {
      "event": "message_updated",
      "pid": 12345,
      "latest_message_id": "...",
      "latest_message_at": 1772119000123,
      "latest_message": "short preview when available"
    }
  ],
  "status": { "...": "full normalized /status payload" }
}
```

---

## GET `/watch/{pid}`
Per-agent watch endpoint.

Supports:
- long-poll JSON (default)
- SSE stream when `Accept: text/event-stream`

### Query params (long-poll)
- `timeout_ms` (optional, default `20000`, range `250..60000`)
- `fingerprint` (optional)

### Long-poll events
- `snapshot`
- `out_of_sync`
- `message_updated`
- `activity_changed`
- `agent_updated`
- `agent_gone`
- `timeout`

### Long-poll example
```json
{
  "ok": true,
  "event": "message_updated",
  "pid": 12345,
  "fingerprint": "agent_fp",
  "agent": {
    "pid": 12345,
    "activity": "running",
    "latest_message_id": "...",
    "latest_message_at": 1772119000123
  }
}
```

### SSE format
- HTTP protocol: `HTTP/1.1`
- Content-Type: `text/event-stream`
- Headers include:
  - `Cache-Control: no-cache`
  - `X-Accel-Buffering: no`
- Event names match long-poll event names.
- Payload is emitted as JSON in `data:` lines.
- Each event includes an SSE `id:` field for resume-friendly clients.
- `Last-Event-ID` is honored on reconnect:
  - if it matches current agent state id: stream resumes without duplicate snapshot
  - if it differs: server emits `out_of_sync` with current agent snapshot
- Keepalive comments are sent periodically.

Example frame:
```text
id: 12345:9f8e7d...
event: message_updated
data: {"ok":true,"pid":12345,"fingerprint":"...","agent":{...}}
```

---

## POST `/send`
Send a message to a running Pi agent.

### Body
```json
{
  "pid": 12345,
  "message": "Your message"
}
```

### Success
```json
{
  "ok": true,
  "pid": 12345,
  "delivery": "tmux"
}
```

### Errors
- `400 invalid body/json/pid/message`
- `401 unauthorized`
- `429 send rate limit exceeded`
- `502 daemon unavailable`

---

## Removed endpoints

- `GET /latest/{pid}` (removed in v3)

Use `/status` + `/watch` or `/watch/{pid}` instead.

---

## Suggested client strategy

1. `GET /status` on startup.
2. Keep global state fresh with `GET /watch?fingerprint=<fp>` long-poll loop.
3. For open detail panels, use per-agent watch:
   - long-poll: `GET /watch/{pid}?fingerprint=<agent_fp>`
   - or SSE: `GET /watch/{pid}` with `Accept: text/event-stream`.
4. Use `POST /send` for replies/actions.
