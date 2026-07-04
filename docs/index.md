# 📻 pyrtl_433

A standalone, dependency-light async client for the rtl_433 HTTP server's
WebSocket + `/cmd` API.

`pyrtl_433` speaks the *transport/protocol* half of an rtl_433 receiver: it
connects to one server over a WebSocket, parses the JSON event stream, and drives
the HTTP `/cmd` endpoint that reports and controls the SDR configuration.

## What it does

- Streams decoded device events as **normalized** `NormalizedEvent` objects — a
  stable per-device key plus the measurement fields, with identity and skip keys
  split out.
- **Classifies replays.** On every (re)connect the server replays up to its last
  100 events; each event is tagged `is_replay` so a consumer can seed values
  without re-firing on already-seen or stale-gap frames.
- Keeps live snapshots of the server's SDR/meta config, throughput stats, and
  device identity, re-fetched over HTTP on a timer so they stay current
  independently of the socket.
- Exposes the `/cmd` **setter primitive** (`_send_cmd`) plus the `sdr` module's
  pure command transforms, so you can retune, set gain, change sample rate, etc.
- Reconnects on drop with capped exponential backoff, and tolerates keep-alives,
  malformed JSON, and hidden `/cmd` endpoints without ever killing the loop.

## Next steps

- [Getting Started](quickstart.md) — install, then connect and consume events in
  a few lines.
- [API Reference](api-reference.md) — the client constructor, runtime snapshots,
  and module map.
- [Protocol Reference](protocol-reference.md) — the rtl_433 server's
  WebSocket/HTTP API this client speaks.
- [Development](development.md) — testing and the mutation-score contract.
