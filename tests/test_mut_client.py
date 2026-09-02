"""Mutation-killing tests for :class:`pyrtl_433.client.Rtl433Client`.

This tier asserts *exact* request params, *exact* stored values, and *both*
branches of every conditional the client owns, so a small mutation (a flipped
comparison, a dropped ``str()``/``int()``, a wrong dict key, an omitted param, a
broadened ``except``) trips at least one assertion. Its sibling
``test_mut_client_floor.py`` covers the timing/lifecycle paths.

Rewritten from tests/test_mut_coordinator_base.py of rtl-433-hass/rtl_433
(Apache-2.0) against the framework-free client + fake session.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import aiohttp
import pytest

from pyrtl_433.client import CannotConnect, Rtl433Client

HOST = "rtl433.local"
CMD_URL = "http://rtl433.local:8433/cmd"


# --------------------------------------------------------------------------- #
# _send_cmd: exact query params for every param shape                          #
# --------------------------------------------------------------------------- #
async def test_send_cmd_gain_string_arg(make_client, fake_session):
    """``set gain "32.8"`` issues GET /cmd?cmd=gain&arg=32.8 (arg sent verbatim)."""
    client = make_client()

    result = await client._send_cmd("gain", arg="32.8")

    assert result is True
    params = fake_session.params_for("gain")
    assert params == {"cmd": "gain", "arg": "32.8"}
    # The request went to the /cmd server root, not the WS path.
    assert fake_session.get_calls[-1].url == CMD_URL


async def test_send_cmd_gain_auto_sends_empty_arg_not_omitted(
    make_client, fake_session
):
    """Gain-auto issues arg="" — the empty string is PRESENT, not omitted."""
    client = make_client()

    result = await client._send_cmd("gain", arg="")

    assert result is True
    params = fake_session.params_for("gain")
    assert "arg" in params  # kills the "drop empty arg" mutant
    assert params["arg"] == ""
    assert params == {"cmd": "gain", "arg": ""}


async def test_send_cmd_val_stringifies_int(make_client, fake_session):
    """A ``val`` command sends ``val`` as the stringified integer."""
    client = make_client()

    result = await client._send_cmd("center_frequency", val=433920000)

    assert result is True
    params = fake_session.params_for("center_frequency")
    assert params == {"cmd": "center_frequency", "val": "433920000"}
    assert params["val"] == "433920000"  # str, not int
    assert isinstance(params["val"], str)


async def test_send_cmd_val_zero_is_sent(make_client, fake_session):
    """val=0 is sent as "0" (0 is not None: kills an ``is not None`` -> truthiness flip)."""
    client = make_client()

    await client._send_cmd("ppm_error", val=0)

    params = fake_session.params_for("ppm_error")
    assert params == {"cmd": "ppm_error", "val": "0"}


async def test_send_cmd_no_val_no_arg_sends_only_cmd(make_client, fake_session):
    """With neither val nor arg, only ``cmd`` is sent (no stray val/arg keys)."""
    client = make_client()

    await client._send_cmd("start")

    params = fake_session.params_for("start")
    assert params == {"cmd": "start"}
    assert "val" not in params
    assert "arg" not in params


async def test_send_cmd_returns_true_on_2xx_false_on_error(
    make_client, fake_session, make_response
):
    """_send_cmd returns True on a 2xx and False when raise_for_status raises."""
    client = make_client()

    # Success path.
    assert await client._send_cmd("ppm_error", val=5) is True

    # Failure path: map the command to a 500 response.
    fake_session.cmd_responses["ppm_error"] = make_response(status=500)
    assert await client._send_cmd("ppm_error", val=5) is False


async def test_send_cmd_secure_uses_https_and_getter_timeout(make_client, fake_session):
    """A secure client sends the /cmd write over https with the 10s getter timeout."""
    client = make_client(secure=True)

    await client._send_cmd("gain", arg="")

    call = fake_session.get_calls[-1]
    assert call.url == "https://rtl433.local:8433/cmd"  # secure propagated
    assert call.kwargs["timeout"].total == 10.0  # ClientTimeout(total=_GETTER_TIMEOUT)


# --------------------------------------------------------------------------- #
# _fetch_cmd + unwrap_result: wrapped vs bare, non-200                          #
# --------------------------------------------------------------------------- #
async def test_fetch_cmd_returns_wrapped_payload(make_client, fake_session):
    """_fetch_cmd returns the parsed body verbatim (envelope intact)."""
    fake_session.cmd_responses["get_gain"] = {"result": "32.8"}
    client = make_client()

    payload = await client._fetch_cmd("get_gain")

    assert payload == {"result": "32.8"}
    # It targeted /cmd?cmd=get_gain at the server root.
    assert fake_session.get_calls[-1].url == CMD_URL
    assert fake_session.params_for("get_gain") == {"cmd": "get_gain"}


async def test_fetch_cmd_returns_bare_payload(make_client, fake_session):
    """A bare (un-enveloped) payload is returned as-is."""
    fake_session.cmd_responses["get_gain"] = "40"
    client = make_client()

    assert await client._fetch_cmd("get_gain") == "40"


async def test_fetch_cmd_non_200_returns_none(make_client, fake_session, make_response):
    """A non-200 response makes _fetch_cmd return None."""
    fake_session.cmd_responses["get_meta"] = make_response(status=503)
    client = make_client()

    assert await client._fetch_cmd("get_meta") is None


async def test_fetch_cmd_secure_uses_https_and_getter_timeout(
    make_client, fake_session
):
    """A secure client reads /cmd over https with the 10s getter timeout."""
    fake_session.cmd_responses["get_gain"] = {"result": "40"}
    client = make_client(secure=True)

    await client._fetch_cmd("get_gain")

    call = fake_session.get_calls[-1]
    assert call.url == "https://rtl433.local:8433/cmd"  # secure propagated
    assert call.kwargs["timeout"].total == 10.0  # ClientTimeout(total=_GETTER_TIMEOUT)


# --------------------------------------------------------------------------- #
# refresh_meta: hop_interval derivation, gain/ppm type guards                   #
# --------------------------------------------------------------------------- #
def _meta(**result):
    return {"result": result}


async def test_refresh_meta_hop_interval_from_first_hop_time(make_client, fake_session):
    """hop_interval == hop_times[0] (not [-1], not len)."""
    fake_session.cmd_responses.update(
        {
            "get_meta": _meta(hop_times=[300, 999, 42]),
            "get_gain": {"result": None},
            "get_ppm_error": {"result": None},
        }
    )
    client = make_client()

    await client.refresh_meta()

    assert client.meta["hop_interval"] == 300


async def test_refresh_meta_empty_hop_times_no_hop_interval(make_client, fake_session):
    """An empty hop_times list produces no hop_interval key."""
    fake_session.cmd_responses.update(
        {
            "get_meta": _meta(center_frequency=1, hop_times=[]),
            "get_gain": {"result": None},
            "get_ppm_error": {"result": None},
        }
    )
    client = make_client()

    await client.refresh_meta()

    assert "hop_interval" not in client.meta


async def test_refresh_meta_no_hop_times_key_no_hop_interval(make_client, fake_session):
    """A meta without hop_times produces no hop_interval."""
    fake_session.cmd_responses.update(
        {
            "get_meta": _meta(center_frequency=1),
            "get_gain": {"result": None},
            "get_ppm_error": {"result": None},
        }
    )
    client = make_client()

    await client.refresh_meta()

    assert "hop_interval" not in client.meta


async def test_refresh_meta_gain_string_stored(make_client, fake_session):
    """A gain string is stored under meta['gain']."""
    fake_session.cmd_responses.update(
        {
            "get_meta": _meta(center_frequency=1),
            "get_gain": {"result": "32.8"},
            "get_ppm_error": {"result": None},
        }
    )
    client = make_client()

    await client.refresh_meta()

    assert client.meta["gain"] == "32.8"


async def test_refresh_meta_int_gain_not_stored(make_client, fake_session):
    """A non-string (int) gain is NOT stored (isinstance str guard)."""
    fake_session.cmd_responses.update(
        {
            "get_meta": _meta(center_frequency=1),
            "get_gain": {"result": 40},
            "get_ppm_error": {"result": None},
        }
    )
    client = make_client()

    await client.refresh_meta()

    assert "gain" not in client.meta


async def test_refresh_meta_ppm_int_stored_including_zero(make_client, fake_session):
    """ppm_error int (including 0) is stored."""
    fake_session.cmd_responses.update(
        {
            "get_meta": _meta(center_frequency=1),
            "get_gain": {"result": None},
            "get_ppm_error": {"result": 0},
        }
    )
    client = make_client()

    await client.refresh_meta()

    assert client.meta["ppm_error"] == 0


async def test_refresh_meta_ppm_bool_excluded(make_client, fake_session):
    """A bool ppm_error is excluded (bool is an int subclass; kills the guard drop)."""
    fake_session.cmd_responses.update(
        {
            "get_meta": _meta(center_frequency=1),
            "get_gain": {"result": None},
            "get_ppm_error": {"result": True},
        }
    )
    client = make_client()

    await client.refresh_meta()

    assert "ppm_error" not in client.meta


async def test_refresh_meta_only_known_keys_extracted(make_client, fake_session):
    """Unknown meta keys are dropped; known keys pass through."""
    fake_session.cmd_responses.update(
        {
            "get_meta": _meta(
                center_frequency=433920000,
                samp_rate=250000,
                conversion_mode=1,
                frequencies=[1, 2],
                hop_times=[10],
                bogus_key="nope",
            ),
            "get_gain": {"result": None},
            "get_ppm_error": {"result": None},
        }
    )
    client = make_client()

    await client.refresh_meta()

    assert "bogus_key" not in client.meta
    assert client.meta["conversion_mode"] == 1
    assert client.meta["frequencies"] == [1, 2]


async def test_refresh_meta_merges_and_fires_hub_update_once(make_client, fake_session):
    """New keys merge into existing meta; on_hub_update fires exactly once."""
    fake_session.cmd_responses.update(
        {
            "get_meta": _meta(center_frequency=500000000),
            "get_gain": {"result": "40"},
            "get_ppm_error": {"result": None},
        }
    )
    calls: list[int] = []
    client = make_client(on_hub_update=lambda: calls.append(1))
    client.meta = {"gain": "32.8", "old": "keep"}

    await client.refresh_meta()

    assert client.meta["center_frequency"] == 500000000
    assert client.meta["gain"] == "40"  # updated
    assert client.meta["old"] == "keep"  # preserved
    assert len(calls) == 1


async def test_refresh_meta_no_data_no_hub_update(make_client, fake_session):
    """When all getters yield nothing usable, meta is untouched and no signal fires."""
    fake_session.cmd_responses.update(
        {
            "get_meta": {"result": None},
            "get_gain": {"result": None},
            "get_ppm_error": {"result": None},
        }
    )
    calls: list[int] = []
    client = make_client(on_hub_update=lambda: calls.append(1))

    await client.refresh_meta()

    assert client.meta == {}
    assert calls == []


# --------------------------------------------------------------------------- #
# refresh_stats: dict-only guard                                               #
# --------------------------------------------------------------------------- #
async def test_refresh_stats_dict_stored_and_fires(make_client, fake_session):
    """A dict stats payload is stored and fires on_hub_update."""
    fake_session.cmd_responses["get_stats"] = {"result": {"frames": 3}}
    calls: list[int] = []
    client = make_client(on_hub_update=lambda: calls.append(1))

    await client.refresh_stats()

    assert client.stats == {"frames": 3}
    assert len(calls) == 1


async def test_refresh_stats_non_dict_not_stored(make_client, fake_session):
    """A non-dict stats payload is ignored (isinstance dict guard); no signal."""
    fake_session.cmd_responses["get_stats"] = {"result": "not-a-dict"}
    calls: list[int] = []
    client = make_client(on_hub_update=lambda: calls.append(1))
    client.stats = {"old": "data"}

    await client.refresh_stats()

    assert client.stats == {"old": "data"}
    assert calls == []


async def test_refresh_stats_none_not_stored(make_client, fake_session):
    """A failed (None) stats getter leaves stats unchanged; no signal."""
    fake_session.cmd_responses["get_stats"] = {"result": None}
    calls: list[int] = []
    client = make_client(on_hub_update=lambda: calls.append(1))
    client.stats = {"old": "data"}

    await client.refresh_stats()

    assert client.stats == {"old": "data"}
    assert calls == []


# --------------------------------------------------------------------------- #
# refresh_dev_info: change detection fires on_hub_update only on change         #
# --------------------------------------------------------------------------- #
async def test_refresh_dev_info_fires_only_on_change(make_client, fake_session):
    """dev_info/dev_query changes fire on_hub_update; an unchanged re-fetch does not."""
    fake_session.cmd_responses.update(
        {
            "get_dev_info": {"result": {"vendor": "Realtek"}},
            "get_dev_query": {"result": "0"},
        }
    )
    calls: list[int] = []
    client = make_client(on_hub_update=lambda: calls.append(1))

    await client.refresh_dev_info()
    assert client.dev_info == {"vendor": "Realtek"}
    assert client.dev_query == "0"
    assert len(calls) == 1  # fired on first (changed) fetch

    # Identical re-fetch -> no change -> no signal.
    await client.refresh_dev_info()
    assert len(calls) == 1


async def test_refresh_dev_info_malformed_json_string_ignored(
    make_client, fake_session
):
    """A dev_info that is a non-JSON string is discarded; the query still updates."""
    fake_session.cmd_responses.update(
        {
            "get_dev_info": {"result": "{not valid json"},
            "get_dev_query": {"result": "sel-0"},
        }
    )
    client = make_client()

    await client.refresh_dev_info()

    assert client.dev_info == {}  # malformed string -> None -> not stored
    assert client.dev_query == "sel-0"  # the (valid) query still updates


async def test_refresh_dev_info_empty_values_left_untouched(make_client, fake_session):
    """Empty info dict / empty query string do not overwrite existing state."""
    fake_session.cmd_responses.update(
        {"get_dev_info": {"result": {}}, "get_dev_query": {"result": ""}}
    )
    calls: list[int] = []
    client = make_client(on_hub_update=lambda: calls.append(1))
    client.dev_info = {"vendor": "existing"}
    client.dev_query = "existing-query"

    await client.refresh_dev_info()

    assert client.dev_info == {"vendor": "existing"}
    assert client.dev_query == "existing-query"
    assert calls == []


# --------------------------------------------------------------------------- #
# _classify_frame: event vs shutdown vs ignored routing                        #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "frame",
    [
        {"model": "TestModel", "temperature_C": 22.0},
        {"id": 42, "temperature_C": 5.0},
        {"channel": 1, "humidity": 65},
        {"subtype": "A", "rssi": -80},
    ],
)
async def test_classify_frame_routes_events_to_process(make_client, frame):
    """A frame with model OR any identity key routes to _process_event."""
    client = make_client()
    with (
        patch.object(client, "_process_event") as proc,
        patch.object(client, "_handle_shutdown") as shut,
    ):
        client._classify_frame(frame)
    proc.assert_called_once()
    shut.assert_not_called()


async def test_classify_frame_shutdown_routes_to_handle_shutdown(make_client):
    """A shutdown frame routes to _handle_shutdown, not _process_event."""
    client = make_client()
    with (
        patch.object(client, "_process_event") as proc,
        patch.object(client, "_handle_shutdown") as shut,
    ):
        client._classify_frame({"shutdown": "goodbye"})
    proc.assert_not_called()
    shut.assert_called_once()


@pytest.mark.parametrize(
    "frame",
    [
        {"center_frequency": 433920000, "samp_rate": 250000},  # meta
        {"result": "ok"},  # RPC result
        {"model": None, "temperature_C": 5.0},  # model=None, no identity keys
    ],
)
async def test_classify_frame_non_event_ignored(make_client, frame):
    """Meta / result / model=None frames route to neither handler."""
    client = make_client()
    with (
        patch.object(client, "_process_event") as proc,
        patch.object(client, "_handle_shutdown") as shut,
    ):
        client._classify_frame(frame)
    proc.assert_not_called()
    shut.assert_not_called()


# --------------------------------------------------------------------------- #
# _handle_shutdown: both connectivity branches fire on_hub_update              #
# --------------------------------------------------------------------------- #
async def test_handle_shutdown_when_connected(make_client):
    """Shutdown while connected flips to False and fires on_hub_update."""
    calls: list[int] = []
    client = make_client(on_hub_update=lambda: calls.append(1))
    client.connected = True

    client._handle_shutdown()

    assert client.connected is False
    assert len(calls) == 1


async def test_handle_shutdown_when_not_connected(make_client):
    """Shutdown while already disconnected still fires on_hub_update, stays False."""
    calls: list[int] = []
    client = make_client(on_hub_update=lambda: calls.append(1))
    client.connected = False

    client._handle_shutdown()

    assert client.connected is False
    assert len(calls) == 1


# --------------------------------------------------------------------------- #
# _read_frames: routing per WSMsgType                                          #
# --------------------------------------------------------------------------- #
def _text(data):
    return aiohttp.WSMessage(aiohttp.WSMsgType.TEXT, data, "")


async def test_read_frames_processes_text(make_client, make_ws):
    """A TEXT frame is dispatched to _handle_text_frame with its data."""
    client = make_client()
    handled: list[str] = []

    with patch.object(client, "_handle_text_frame", side_effect=handled.append):
        await client._read_frames(make_ws([_text("A"), _text("B")]))

    assert handled == ["A", "B"]


@pytest.mark.parametrize(
    "closing_type",
    [
        aiohttp.WSMsgType.CLOSE,
        aiohttp.WSMsgType.CLOSING,
        aiohttp.WSMsgType.CLOSED,
        aiohttp.WSMsgType.ERROR,
    ],
)
async def test_read_frames_breaks_on_close_and_error(
    make_client, make_ws, closing_type
):
    """CLOSE/CLOSING/CLOSED/ERROR break the loop: frames after them are not read."""
    client = make_client()
    handled: list[str] = []

    messages = [
        _text("before"),
        aiohttp.WSMessage(closing_type, None, ""),
        _text("after"),  # must NOT be processed
    ]
    with patch.object(client, "_handle_text_frame", side_effect=handled.append):
        await client._read_frames(make_ws(messages))

    assert handled == ["before"]


@pytest.mark.parametrize(
    "ignored_type",
    [aiohttp.WSMsgType.PING, aiohttp.WSMsgType.PONG, aiohttp.WSMsgType.BINARY],
)
async def test_read_frames_ignores_keepalives(make_client, make_ws, ignored_type):
    """PING/PONG/BINARY are ignored (skipped) but do NOT break the loop."""
    client = make_client()
    handled: list[str] = []

    messages = [
        aiohttp.WSMessage(ignored_type, b"", ""),
        _text("real"),  # still reached after the ignored keep-alive
    ]
    with patch.object(client, "_handle_text_frame", side_effect=handled.append):
        await client._read_frames(make_ws(messages))

    assert handled == ["real"]


async def test_read_frames_stops_when_stop_event_set(make_client, make_ws):
    """A set stop event breaks the read loop before processing any frame."""
    client = make_client()
    client._stop_event.set()
    handled: list[str] = []

    with patch.object(client, "_handle_text_frame", side_effect=handled.append):
        await client._read_frames(make_ws([_text("A")]))

    assert handled == []


# --------------------------------------------------------------------------- #
# validate_connection: both outcomes                                          #
# --------------------------------------------------------------------------- #
async def test_validate_connection_true_and_closes_ws(fake_session):
    """validate_connection returns True and closes the probe socket."""
    fake_session.default_ws_outcome = []  # a successful connect -> empty FakeWS

    result = await Rtl433Client.validate_connection(fake_session, HOST, 8433, "/ws")

    assert result is True
    # The one probe socket was opened and then closed (no leak).
    assert len(fake_session.ws_instances) == 1
    assert fake_session.ws_instances[0].closed is True
    # It connected to the ws:// URL for these params.
    assert fake_session.ws_connect_calls[-1].url == "ws://rtl433.local:8433/ws"


@pytest.mark.parametrize(
    "error",
    [aiohttp.ClientError("refused"), TimeoutError(), OSError("no route")],
)
async def test_validate_connection_raises_cannot_connect(fake_session, error):
    """ClientError / timeout / OSError each raise CannotConnect."""
    fake_session.default_ws_outcome = error

    with pytest.raises(CannotConnect):
        await Rtl433Client.validate_connection(fake_session, HOST, 8433, "/ws")


async def test_validate_connection_secure_uses_wss(fake_session):
    """secure=True probes a wss:// URL."""
    fake_session.default_ws_outcome = []

    await Rtl433Client.validate_connection(fake_session, HOST, 8433, "/ws", secure=True)

    assert fake_session.ws_connect_calls[-1].url == "wss://rtl433.local:8433/ws"


# --------------------------------------------------------------------------- #
# ws_url property                                                             #
# --------------------------------------------------------------------------- #
async def test_ws_url_property(make_client):
    """The ws_url property reflects host/port/path/secure."""
    assert make_client().ws_url == "ws://rtl433.local:8433/ws"
    secure = make_client(port=9000, path="events", secure=True)
    assert secure.ws_url == "wss://rtl433.local:9000/events"


# --------------------------------------------------------------------------- #
# Initial state                                                               #
# --------------------------------------------------------------------------- #
async def test_injected_skip_keys_are_applied_and_time_always_excluded(
    make_client, fake_clock
):
    """The injected skip set is honored, and ``time`` is force-excluded on top of it.

    Injecting a measurement key (``humidity``) that is NOT in the default skip set
    proves the client passes its own skip set to ``normalize`` (not the default);
    and asserting ``time`` is gone proves the always-added ``time`` skip, since the
    injected set deliberately omits it.
    """
    fake_clock.set(datetime(2026, 5, 25, 10, 0, 0, tzinfo=UTC))
    seen = []
    client = make_client(on_event=seen.append, skip_keys={"humidity"})

    client._handle_text_frame(
        '{"time": "2026-05-25T10:00:00Z", "model": "X", "id": 1, '
        '"temperature_C": 1.0, "humidity": 55}'
    )

    assert len(seen) == 1
    assert "humidity" not in seen[0].fields  # injected skip set was applied
    assert "time" not in seen[0].fields  # time excluded despite skip_keys omitting it
    assert seen[0].fields == {"temperature_C": 1.0}


async def test_default_clock_returns_utc_now(fake_session):
    """With no injected clock, ``_now()`` returns an aware UTC datetime (not None/naive)."""
    from pyrtl_433.client import Rtl433Client

    client = Rtl433Client("rtl433.local", session=fake_session)  # no clock injected

    now = client._now()
    assert isinstance(now, datetime)
    assert (
        now.tzinfo == UTC
    )  # aware, and in UTC (kills naive/None default-clock mutants)


async def test_aiter_returns_self(make_client):
    """``__aiter__`` returns the client itself (it is its own async iterator)."""
    client = make_client()
    assert client.__aiter__() is client


async def test_initial_state(make_client):
    """A freshly built client has empty snapshots and is disconnected."""
    client = make_client()
    assert client.connected is False
    assert client.meta == {}
    assert client.stats == {}
    assert client.dev_info == {}
    assert client.dev_query is None
    assert client._device_state == {}
    assert client.time_precision is None
    assert client._connection_time is None
    assert client._malformed_cmds == set()


def test_cannot_connect_is_runtime_error():
    """CannotConnect is a RuntimeError carrying its message."""
    exc = CannotConnect("Cannot connect to ws://host:8433/ws: timeout")
    assert isinstance(exc, RuntimeError)
    assert "Cannot connect" in str(exc)
