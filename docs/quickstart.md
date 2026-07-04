# Getting Started

## Requirements

- Python >= 3.14
- [`aiohttp`](https://docs.aiohttp.org/)

## Install

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

See the [API Reference](api-reference.md) for the full constructor signature and
the runtime snapshot attributes, and the
[Protocol Reference](protocol-reference.md) for the SDR command set used by the
`/cmd` writes above.
