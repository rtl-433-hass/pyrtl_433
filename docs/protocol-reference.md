# Protocol Reference

Ported subset of the rtl_433 server's WebSocket/HTTP API relevant to this client.

**Starting the server.** `rtl_433 -F http` binds `0.0.0.0:8433` (default port
`8433`). HTTP and WebSocket share the same port.

**Connecting.** Open a WebSocket to the server. Any request carrying a WebSocket
`Upgrade` is handled as a WS connection regardless of path, so the client's
default `/ws` path works. Immediately after the handshake the server pushes, as
text frames, a `meta` object describing the current configuration, then a replay
of up to the **last 100 events** from its in-memory ring buffer, then the live
event stream. (This client sources `meta`/`stats` over HTTP `/cmd` rather than
the pushed frame, and classifies the replayed events via `replay.py`.)

**Commands.** Each command is one JSON object: `{"cmd": "<name>", "arg":
"<string>", "val": <integer>}`. `val` is parsed base-10 as an unsigned 32-bit
integer (floats truncate, negatives wrap). The same command set is reachable over
the WebSocket, over `/cmd` (HTTP GET query or POST form with `cmd`/`arg`/`val`
parameters), and over `/jsonrpc`.

**The `/cmd` result envelope.** Over HTTP `/cmd`, **every** getter reply is
wrapped in `{"result": <value>}` — including the JSON-payload getters
(`get_meta`, `get_stats`, `get_dev_info`) that the WebSocket sends as bare frames.
A client polling over `/cmd` must unwrap `result` for all getters; `_urls.unwrap_result`
does this.

## SDR command set

As used by this client's `sdr` module:

| `cmd` | Argument | Kind | Effect |
| --- | --- | --- | --- |
| `center_frequency` | `val` in Hz | live | Retune center frequency. |
| `sample_rate` | `val` in Hz | live | Set sample rate. |
| `ppm_error` | `val` integer | live | Set frequency correction in ppm. |
| `gain` | `arg` dB string, e.g. `"32.8"`, empty = auto | live | Set tuner gain. |
| `convert` | `val` 0/1/2 (`native`/`si`/`customary`) | config-setter | Set unit conversion mode. |
| `hop_interval` | `val` seconds | config-setter | Set frequency-hop interval. |

Live commands take effect on the running receiver immediately; config-setters
apply on next use. `gain` requires a non-omitted `arg` (an empty string is the
"auto" sentinel, which is why the gain write always passes `arg`).

## meta object

`get_meta`, or pushed on connect:

```json
{
  "frequencies": [...],
  "hop_times": [...],
  "center_frequency": 433920000,
  "samp_rate": 250000,
  "conversion_mode": 0
}
```

`meta` carries neither gain nor ppm — read those from `get_gain` (string, empty
means auto) and `get_ppm_error` (int). `refresh_meta` folds all three together
and derives `hop_interval` from `hop_times[0]`.

## stats object

`get_stats`:

```json
{
  "enabled": 234,
  "since": "2024-01-01T00:00:00",
  "frames": { "count": 0, "fsk": 0, "events": 0 },
  "stats": [ /* per-protocol entries */ ]
}
```

`enabled` counts enabled decoders; `frames.count`/`frames.fsk` are OOK/FSK frame
counts; `frames.events` is the cumulative decoded-event count.

## Event stream

Live decoded records are JSON objects with a `model` key plus device fields
(e.g. `{"time":"...","model":"...","id":...,"temperature_C":...}`). On server
shutdown each socket receives `{"shutdown":"goodbye"}`.

## Security

!!! warning "No authentication — bind carefully"

    The server has no authentication or authorization, binds to all interfaces by
    default, opens CORS fully, and speaks plain HTTP. Any client that can reach the
    port can read the data stream and change live SDR settings. Bind to `127.0.0.1`
    and/or front it with a TLS-and-auth reverse proxy for remote access.
