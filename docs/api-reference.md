# API Reference

## Constructor

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

## Runtime snapshots

Read-only runtime snapshots are exposed as attributes: `client.connected`,
`client.meta`, `client.stats`, `client.dev_info`, `client.dev_query`. Refresh
them on demand with `await client.refresh_meta()` / `refresh_stats()` /
`refresh_dev_info()`.

`client.time_precision` reports the resolution of the server's event `time`
stamps, observed from the frames themselves (`get_meta` carries no timestamp
format, so there is nothing to ask). It is `None` until the first event frame,
then one of:

| Value | Server config | What it means for the consumer |
| --- | --- | --- |
| `TimePrecision.MICROSECOND` | `report_meta time:...usec...` | Two transmissions from one device are always distinguishable. |
| `TimePrecision.SECOND` | rtl_433's default | Two transmissions from one device inside the same wall-clock second carry identical stamps and cannot be told apart by time alone. |
| `TimePrecision.UNUSABLE` | `report_meta time:off`, or a form this parser does not accept | No frame has a usable timestamp, so replay suppression is off entirely — the server's reconnect backlog re-fires in full. |

It is latest-wins, so it clears on the next frame after an operator changes the
server's config, and `on_hub_update` fires whenever it changes. The library only
reports it; deciding whether to surface the server-side remedy (adding
`report_meta time:iso:usec:tz`) is the consumer's call.

## Module map

| Module | Responsibility |
| --- | --- |
| `client.py` | `Rtl433Client` — the async WebSocket + `/cmd` transport: connect/reconnect loop, event dispatch, HTTP getters/setters, `validate_connection`. |
| `normalizer.py` | Split a raw event into a deterministic device key + identity/measurement fields (`normalize`, `device_key`, `NormalizedEvent`). |
| `naming.py` | Presentation helpers built on the device key: the `safe_token` builder it is made of, plus `display_name` and `identity_suffix` (the model-stripped id suffix). |
| `replay.py` | Reconnect-replay classifier (`classify_replay`, `ReplayVerdict`), `parse_event_time` timestamp parsing, and `time_precision` / `TimePrecision` stamp-resolution reporting. |
| `sdr.py` | Pure SDR `/cmd` command transforms: the command registry, value read/convert helpers, and `gain_command_arg`. |
| `library/` | The data-driven device library: YAML field mappings (`library/data/*.yaml`) loaded into a `Registry` of `FieldDescriptor`s, plus `lookup`, `should_skip`, `apply_transform`, and the user-override merge. See the [Device Library](device-library.md) reference. |
| `availability.py` | Event-driven device classification (`is_event_driven`, `known_field_keys`) — the pure half of an availability-timeout policy. |
| `_urls.py` | WebSocket/`/cmd` URL builders and the `{"result": ...}` getter-response unwrap. |

## Device library

`load_library()` parses the packaged YAML into `(Registry, skip_keys)`;
`lookup(field_key, model, registry=...)` resolves a `FieldDescriptor`
(model-scoped first, then global) and `apply_transform(descriptor, raw_value)`
converts a raw rtl_433 value into the state to store.

```python
from pyrtl_433 import apply_transform, load_library, lookup, normalize

registry, skip_keys = load_library()
normalized = normalize(event, skip_keys)
for field_key, raw_value in normalized.fields.items():
    if descriptor := lookup(field_key, normalized.model, registry=registry):
        state = apply_transform(descriptor, raw_value)
```

`FieldDescriptor`, `Registry`, `load_library`, `lookup` and `apply_transform` are
re-exported at the package top level; the rest of the surface
(`merge_overrides`, `validate_user_mappings`, `normalize_overrides`,
`event_driven_field_keys`, `should_skip`, `USER_OVERRIDE_FILENAME`) is imported
from `pyrtl_433.library`. The YAML schema, the resolution order, and the
override merge semantics are documented in
[Device Library](device-library.md).

## Availability classification

`pyrtl_433` ships the classifier, not the timeout policy — the consumer maps the
boolean onto its own defaults (typically never-expire for event-driven devices,
a finite default for periodic ones).

```python
from pyrtl_433 import is_event_driven, known_field_keys
from pyrtl_433.library import event_driven_field_keys

event_keys = event_driven_field_keys(registry)
fields = known_field_keys(adopted_field_keys, latest_payload_field_keys)
never_expires = is_event_driven(fields, event_keys)
```

`known_field_keys` unions the persisted (restart-surviving) field keys with the
latest live payload's, so a device that has been silent since a restart is still
classified from what it reported before. An empty `event_keys` yields `False`, so
a failed library load degrades to the periodic default.
