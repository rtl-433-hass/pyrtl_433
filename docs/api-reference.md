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

## Module map

| Module | Responsibility |
| --- | --- |
| `client.py` | `Rtl433Client` — the async WebSocket + `/cmd` transport: connect/reconnect loop, event dispatch, HTTP getters/setters, `validate_connection`. |
| `normalizer.py` | Split a raw event into a deterministic device key + identity/measurement fields (`normalize`, `device_key`, `NormalizedEvent`). |
| `replay.py` | Reconnect-replay classifier (`classify_replay`, `ReplayVerdict`) and `parse_event_time` timestamp parsing. |
| `sdr.py` | Pure SDR `/cmd` command transforms: the command registry, value read/convert helpers, and `gain_command_arg`. |
| `_urls.py` | WebSocket/`/cmd` URL builders and the `{"result": ...}` getter-response unwrap. |
