# 📻 pyrtl_433

A standalone, dependency-light async client for the rtl_433 HTTP server's
WebSocket + `/cmd` API.

`pyrtl_433` speaks the *transport/protocol* half of an rtl_433 receiver: it
connects to one server over a WebSocket, parses the JSON event stream, and drives
the HTTP `/cmd` endpoint that reports and controls the SDR configuration. It also
ships the **device library** — the YAML field mappings that say what each rtl_433
field means (`pyrtl_433.library`) — plus the pure event-driven classifier an
availability policy needs (`pyrtl_433.availability`).

Deliberate non-scope: this is a client and a data library, not a policy engine.
It does **not** include the Home Assistant integration's entity model,
desired-state store, SDR adoption/enforcement, or availability watchdog. It emits
normalized, replay-classified events, resolves each field to a descriptor and a
transformed value, and gives you the `/cmd` setter primitive plus the SDR value
transforms — you build any higher-level policy on top.

**Full documentation:** <https://rtl-433-hass.github.io/pyrtl_433/latest/>

## Install

This project is [uv](https://docs.astral.sh/uv/)-first and is published on
[PyPI](https://pypi.org/project/pyrtl_433/):

```sh
uv add pyrtl_433
```

From a clone of this repository: `uv pip install .`. See
[Getting Started](https://rtl-433-hass.github.io/pyrtl_433/latest/quickstart/)
for pip and other options.

## Quick start

Inject an `aiohttp.ClientSession`, construct the client, and consume events with
`async for event in client` (or an `on_event` callback):

```python
import asyncio

import aiohttp

from pyrtl_433 import Rtl433Client


async def main() -> None:
    async with aiohttp.ClientSession() as session:
        client = Rtl433Client("192.0.2.10", session=session)
        await client.start()
        try:
            async for event in client:
                if event.is_replay:
                    continue  # already-seen / stale-gap frame; seed only, don't act
                print(event.device_key, event.model, event.fields)
        finally:
            await client.stop()


asyncio.run(main())
```

See the
[Quick Start](https://rtl-433-hass.github.io/pyrtl_433/latest/quickstart/) guide
for the callback style, the full constructor signature, and SDR `/cmd` writes.

## Documentation

- [Full documentation](https://rtl-433-hass.github.io/pyrtl_433/latest/)
- [Quick Start](https://rtl-433-hass.github.io/pyrtl_433/latest/quickstart/)
- [API Reference](https://rtl-433-hass.github.io/pyrtl_433/latest/api-reference/)
- [Device Library](https://rtl-433-hass.github.io/pyrtl_433/latest/device-library/)
- [Protocol Reference](https://rtl-433-hass.github.io/pyrtl_433/latest/protocol-reference/)
- [Development](https://rtl-433-hass.github.io/pyrtl_433/latest/development/)
- [Issue tracker](https://github.com/rtl-433-hass/pyrtl_433/issues)

## License

Apache-2.0. This library was extracted from the transport/protocol code and the
device-mapping library of the rtl-433-hass/rtl_433 Home Assistant integration. See [`LICENSE`](LICENSE) and
[`NOTICE`](NOTICE) for the source modules and attribution.
