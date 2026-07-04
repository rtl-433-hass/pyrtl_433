# 📻 pyrtl_433

A standalone, dependency-light async client for the rtl_433 HTTP server's
WebSocket + `/cmd` API.

## Overview

`pyrtl_433` speaks the *transport/protocol* half of an rtl_433 receiver: it
connects to one server over a WebSocket, parses the JSON event stream, and drives
the HTTP `/cmd` endpoint that reports and controls the SDR configuration.

What it does:

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

Deliberate non-scope: this is a client, not a policy engine. It does **not**
include the Home Assistant integration's entity model, desired-state store, SDR
adoption/enforcement, or availability watchdog. It emits normalized,
replay-classified events and gives you the `/cmd` setter primitive plus the SDR
value transforms — you build any higher-level policy on top.

## Requirements

- Python >= 3.14
- [`aiohttp`](https://docs.aiohttp.org/)

## Install

This project is [uv](https://docs.astral.sh/uv/)-first. Not yet published to
PyPI; once it is:

```sh
uv add pyrtl_433
```

From a clone of this repository:

```sh
uv pip install .
```

Using pip instead of uv:

```sh
pip install pyrtl_433   # once published
pip install .           # from a clone
```

## Quick start

Inject your own `aiohttp.ClientSession`, construct the client, and consume events
either with `async for event in client` or via an `on_event` callback.

```python
import asyncio

import aiohttp

from pyrtl_433 import Rtl433Client
from pyrtl_433.sdr import gain_command_arg


async def main() -> None:
    async with aiohttp.ClientSession() as session:
        # Optional: probe reachability before starting the connect loop.
        # (session, host, port, path) are positional; secure is keyword-only.
        await Rtl433Client.validate_connection(session, "192.0.2.10", 8433, "/ws")

        client = Rtl433Client("192.0.2.10", session=session)
        await client.start()

        try:
            async for event in client:
                # NormalizedEvent: device_key, model, identity, fields,
                # is_replay, event_time.
                if event.is_replay:
                    continue  # already-seen / stale-gap frame; seed only, don't act
                print(event.device_key, event.model, event.fields)

                # Issue an SDR write via the sdr helpers + the /cmd setter
                # primitive. gain_command_arg("" == auto) composes the arg;
                # an empty string means auto gain.
                arg = gain_command_arg(32.8, gain_auto=False)
                await client._send_cmd("gain", arg=arg)

                # val-based commands (Hz / integer) go the same way, e.g.:
                #   await client._send_cmd("center_frequency", val=433_920_000)
        finally:
            await client.stop()


asyncio.run(main())
```

Callback style instead of the async iterator:

```python
def on_event(event):
    print(event.device_key, event.fields, "replay" if event.is_replay else "live")


client = Rtl433Client("192.0.2.10", session=session, on_event=on_event)
```

Constructor signature:

```python
Rtl433Client(
    host,
    *,
    port=8433,
    path="/ws",
    secure=False,          # ws->wss / http->https for both the socket and /cmd
    session=None,          # inject one, or None to have the client own+close one
    skip_keys=None,        # measurement keys to drop from fields (time always dropped)
    on_event=None,         # sync or async callback receiving each NormalizedEvent
    on_hub_update=None,    # fires on connect/meta/stats/identity change
    clock=None,            # injectable now() -> datetime, for tests
)
```

Read-only runtime snapshots are exposed as attributes: `client.connected`,
`client.meta`, `client.stats`, `client.dev_info`, `client.dev_query`. Refresh
them on demand with `await client.refresh_meta()` / `refresh_stats()` /
`refresh_dev_info()`.

## Module map

| Module | Responsibility |
| --- | --- |
| `client.py` | `Rtl433Client` — the async WebSocket + `/cmd` transport: connect/reconnect loop, event dispatch, HTTP getters/setters, `validate_connection`. |
| `normalizer.py` | Split a raw event into a deterministic device key + identity/measurement fields (`normalize`, `device_key`, `NormalizedEvent`). |
| `replay.py` | Reconnect-replay classifier (`classify_replay`, `ReplayVerdict`) and `parse_event_time` timestamp parsing. |
| `sdr.py` | Pure SDR `/cmd` command transforms: the command registry, value read/convert helpers, and `gain_command_arg`. |
| `_urls.py` | WebSocket/`/cmd` URL builders and the `{"result": ...}` getter-response unwrap. |

## Protocol reference

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

**SDR command set** (as used by this client's `sdr` module):

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

**meta object** (`get_meta`, or pushed on connect):

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

**stats object** (`get_stats`):

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

**Event stream.** Live decoded records are JSON objects with a `model` key plus
device fields (e.g. `{"time":"...","model":"...","id":...,"temperature_C":...}`).
On server shutdown each socket receives `{"shutdown":"goodbye"}`.

**Security.** The server has no authentication or authorization, binds to all
interfaces by default, opens CORS fully, and speaks plain HTTP. Any client that
can reach the port can read the data stream and change live SDR settings. Bind to
`127.0.0.1` and/or front it with a TLS-and-auth reverse proxy for remote access.

## Testing & mutation contract

Tests follow a three-tier naming convention:

- `test_*` — behavioural unit tests of the public API.
- `test_mut_*` — mutation-killing tests written to pin specific mutants.
- `test_mut_*_floor` — the floor tests that hold a module's mutation score at or
  above its ratchet baseline.

The library holds a per-module mutation-score **floor** (killed / total mutants),
enforced by the ratchet:

| Module | Killed / total | Score |
| --- | --- | --- |
| `client.py` | 453 / 459 | 0.987 |
| `normalizer.py` | 74 / 75 | 0.987 |
| `replay.py` | 100 / 101 | 0.990 |
| `sdr.py` | 68 / 70 | 0.971 |
| `_urls.py` | 22 / 22 | 1.000 |
| **Overall** | **717 / 727** | **0.986** |

Local commands (uv-first — there are no `requirements*.txt` files; dev/test
tooling lives in `pyproject.toml`'s `[dependency-groups]` and is locked in
`uv.lock`):

```sh
uv sync --dev            # create the venv + install dev/test tooling from uv.lock
# or: bash scripts/setup.sh

uv run pytest -n auto                                   # run the test suite (parallel)
uv run ruff check . && uv run ruff format --check .     # lint + format
uv run mypy pyrtl_433/                                  # strict type check
uv run mutmut run                                       # mutation testing
uv run python scripts/mutation_stats.py > stats.json    # collect per-module stats
uv run python scripts/mutation_ratchet.py --mode floor --stats stats.json  # enforce the floor
```

Continuous integration runs the same gates (lint, format, strict mypy, tests
with a 95% coverage floor, and the mutation-score ratchet) on every push and pull
request via GitHub Actions — see [`.github/workflows/`](.github/workflows/).

## License

Apache-2.0. This library was extracted from the transport/protocol code of the
rtl-433-hass/rtl_433 Home Assistant integration. See [`LICENSE`](LICENSE) and
[`NOTICE`](NOTICE) for the source modules and attribution.
