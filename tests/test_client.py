"""Behavioral tests for :class:`pyrtl_433.client.Rtl433Client`.

These drive the client's public surface against the scripted ``FakeSession`` from
``conftest`` — the critical paths a consumer depends on: seeding meta/stats/device
identity over ``/cmd``, emitting normalized/replay-classified events (via both the
``on_event`` callback and the ``async for`` iterator), shutdown handling, and the
frame-drop rules for junk frames. Exact-value / both-branch mutation rigor lives
in ``test_mut_client.py`` and ``test_mut_client_floor.py``; this file exercises the
end-to-end behaviour, including one full connect-loop integration test.

Rewritten from the coordinator scenarios in tests/test_coordinator.py of
rtl-433-hass/rtl_433 (Apache-2.0) against the framework-free client + fake session
(no hass, no MockConfigEntry).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json

import aiohttp
import pytest

from pyrtl_433.normalizer import NormalizedEvent


# --------------------------------------------------------------------------- #
# refresh_meta / refresh_stats / refresh_dev_info: /cmd snapshot assembly      #
# --------------------------------------------------------------------------- #
async def test_refresh_meta_assembles_snapshot(make_client, fake_session):
    """refresh_meta unwraps the result envelope and assembles the meta snapshot."""
    fake_session.cmd_responses.update(
        {
            "get_meta": {
                "result": {
                    "center_frequency": 433920000,
                    "samp_rate": 250000,
                    "conversion_mode": 0,
                    "frequencies": [433920000],
                    "hop_times": [300, 999],
                }
            },
            "get_gain": {"result": "32.8"},
            "get_ppm_error": {"result": 5},
        }
    )
    client = make_client()

    await client.refresh_meta()

    assert client.meta["center_frequency"] == 433920000
    assert client.meta["samp_rate"] == 250000
    assert client.meta["conversion_mode"] == 0
    assert client.meta["frequencies"] == [433920000]
    assert client.meta["hop_interval"] == 300  # derived from hop_times[0]
    assert client.meta["gain"] == "32.8"
    assert client.meta["ppm_error"] == 5


async def test_refresh_stats_stores_dict(make_client, fake_session):
    """refresh_stats unwraps and stores the stats dict."""
    fake_session.cmd_responses["get_stats"] = {
        "result": {"frames": {"events": 12, "count": 340}}
    }
    client = make_client()

    await client.refresh_stats()

    assert client.stats == {"frames": {"events": 12, "count": 340}}


async def test_refresh_dev_info_stores_identity(make_client, fake_session):
    """refresh_dev_info stores the USB device label and the -d selector."""
    fake_session.cmd_responses.update(
        {
            "get_dev_info": {
                "result": {"vendor": "Realtek", "product": "RTL2838", "serial": "00"}
            },
            "get_dev_query": {"result": "rtl_tcp"},
        }
    )
    client = make_client()

    await client.refresh_dev_info()

    assert client.dev_info == {
        "vendor": "Realtek",
        "product": "RTL2838",
        "serial": "00",
    }
    assert client.dev_query == "rtl_tcp"


async def test_refresh_dev_info_accepts_json_string_payload(make_client, fake_session):
    """A JSON-string dev_info payload (proxy / WS framing) is parsed to a dict."""
    fake_session.cmd_responses.update(
        {
            "get_dev_info": {"result": '{"vendor": "Nooelec", "serial": "7"}'},
            "get_dev_query": {"result": ""},
        }
    )
    client = make_client()

    await client.refresh_dev_info()

    assert client.dev_info == {"vendor": "Nooelec", "serial": "7"}
    # Empty query string is not stored.
    assert client.dev_query is None


# --------------------------------------------------------------------------- #
# Event emission: on_event callback and async-for iterator                     #
# --------------------------------------------------------------------------- #
async def test_live_event_emitted_to_callback(make_client, fake_clock):
    """A live TEXT event frame is normalized and delivered to on_event."""
    fake_clock.set(datetime(2026, 5, 25, 10, 0, 0, tzinfo=UTC))
    seen: list[NormalizedEvent] = []
    client = make_client(on_event=seen.append)

    client._handle_text_frame(
        '{"time": "2026-05-25T10:00:00Z", "model": "Acurite-606TX", '
        '"id": 42, "temperature_C": 21.37, "humidity": 55}'
    )

    assert len(seen) == 1
    event = seen[0]
    assert isinstance(event, NormalizedEvent)
    assert event.device_key == "Acurite-606TX-42"
    assert event.model == "Acurite-606TX"
    assert event.fields == {"temperature_C": 21.37, "humidity": 55}
    assert event.is_replay is False
    assert event.event_time == datetime(2026, 5, 25, 10, 0, 0, tzinfo=UTC)


async def test_event_emitted_to_async_iterator(make_client, fake_clock):
    """The same frame reaches a consumer iterating with ``async for``/__anext__."""
    fake_clock.set(datetime(2026, 5, 25, 10, 0, 0, tzinfo=UTC))
    client = make_client()

    client._handle_text_frame(
        '{"time": "2026-05-25T10:00:00Z", "model": "Foo", "channel": 3, "humidity": 47}'
    )

    event = await asyncio.wait_for(client.__anext__(), timeout=1)
    assert event.device_key == "Foo-ch3"
    assert event.fields == {"humidity": 47}


async def test_replay_frame_flagged_is_replay(make_client, fake_clock):
    """A frame at/below the high-water mark is emitted with is_replay=True."""
    fake_clock.set(datetime(2026, 5, 25, 10, 0, 5, tzinfo=UTC))
    seen: list[NormalizedEvent] = []
    client = make_client(on_event=seen.append)
    # Pretend we have already seen an event at 10:00:00.
    client._event_high_water = datetime(2026, 5, 25, 10, 0, 0, tzinfo=UTC)

    client._handle_text_frame(
        '{"time": "2026-05-25T10:00:00Z", "model": "Acurite-606TX", '
        '"id": 42, "temperature_C": 21.37}'
    )

    assert len(seen) == 1
    assert seen[0].is_replay is True  # still emitted so it can seed values


async def test_stale_gap_frame_flagged_is_replay(make_client, fake_clock):
    """A never-seen but old frame is emitted as a stale-gap replay (is_replay=True)."""
    fake_clock.set(datetime(2026, 5, 25, 10, 5, 0, tzinfo=UTC))  # 5 min after
    seen: list[NormalizedEvent] = []
    client = make_client(on_event=seen.append)

    client._handle_text_frame(
        '{"time": "2026-05-25T10:00:00Z", "model": "Foo", "id": 1, '
        '"temperature_C": 5.0}'
    )

    assert len(seen) == 1
    assert seen[0].is_replay is True
    # The high-water mark advanced to the gap event's time.
    assert client._event_high_water == datetime(2026, 5, 25, 10, 0, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Shutdown handling                                                            #
# --------------------------------------------------------------------------- #
async def test_shutdown_frame_flips_connectivity_and_fires_hub_update(make_client):
    """A ``{"shutdown": ...}`` frame flips connected off and fires on_hub_update."""
    hub_updates: list[int] = []
    client = make_client(on_hub_update=lambda: hub_updates.append(1))
    client.connected = True

    client._handle_text_frame('{"shutdown": "goodbye"}')

    assert client.connected is False
    assert len(hub_updates) == 1
    # A shutdown frame is not an event: nothing is emitted.
    assert client._queue.empty()


# --------------------------------------------------------------------------- #
# Junk frames are dropped                                                      #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "frame",
    [
        "",  # empty
        "   ",  # whitespace-only (keep-alive)
        "{not valid json",  # malformed JSON
        "[1, 2, 3]",  # valid JSON but not an object
        '"just a string"',  # valid JSON scalar
        "42",  # valid JSON number
        '{"center_frequency": 433920000, "samp_rate": 250000}',  # meta, not an event
    ],
)
async def test_junk_frames_are_dropped(make_client, frame):
    """Empty / whitespace / malformed / non-object / meta frames emit nothing."""
    seen: list[NormalizedEvent] = []
    client = make_client(on_event=seen.append)

    client._handle_text_frame(frame)

    assert seen == []
    assert client._queue.empty()


# --------------------------------------------------------------------------- #
# Full connect-loop integration: connect -> seed -> read live + replay         #
# --------------------------------------------------------------------------- #
async def test_connect_loop_seeds_meta_and_emits_live_then_replay(
    make_client, fake_session, fake_clock
):
    """End-to-end: start() connects, seeds meta over /cmd, then streams frames.

    The first event (stamped == now) is live; the second (older, at/below the
    freshly-advanced high-water mark) is a replay. Both are emitted; only the flag
    differs. Exercises the real connect loop, ``refresh_*`` on connect, the TEXT
    frame path in ``_read_frames``, classification, and emission together.
    """
    fake_clock.set(datetime(2026, 5, 25, 10, 0, 0, tzinfo=UTC))
    fake_session.cmd_responses.update(
        {
            "get_meta": {
                "result": {"center_frequency": 433920000, "samp_rate": 250000}
            },
            "get_stats": {"result": {"frames": {"events": 1}}},
            "get_dev_info": {"result": {"vendor": "Realtek"}},
            "get_dev_query": {"result": "0"},
        }
    )
    # One successful connect streaming: a meta frame (ignored) + a live event +
    # an older replay event. After the list drains the socket closes; the next
    # connect fails so the loop parks on the backoff wait until stop().
    frames = [
        aiohttp.WSMessage(
            aiohttp.WSMsgType.TEXT,
            '{"center_frequency": 433920000}',  # meta frame, not an event
            "",
        ),
        aiohttp.WSMessage(
            aiohttp.WSMsgType.TEXT,
            '{"time": "2026-05-25T10:00:00Z", "model": "Acurite-606TX", '
            '"id": 42, "temperature_C": 21.0}',  # live
            "",
        ),
        aiohttp.WSMessage(
            aiohttp.WSMsgType.TEXT,
            '{"time": "2026-05-25T09:59:59Z", "model": "Acurite-606TX", '
            '"id": 42, "temperature_C": 20.5}',  # older -> replay
            "",
        ),
    ]
    fake_session.ws_outcomes.append(frames)
    fake_session.default_ws_outcome = aiohttp.ClientError("down")

    seen: list[NormalizedEvent] = []
    client = make_client(on_event=seen.append)

    await client.start()
    live = await asyncio.wait_for(client.__anext__(), timeout=1)
    replay = await asyncio.wait_for(client.__anext__(), timeout=1)
    await client.stop()

    # Meta was seeded over /cmd during the connect handshake.
    assert client.meta["center_frequency"] == 433920000
    assert client.stats == {"frames": {"events": 1}}
    assert client.dev_info == {"vendor": "Realtek"}

    # First event is live, second is a replay; both were emitted.
    assert live.is_replay is False
    assert live.fields == {"temperature_C": 21.0}
    assert replay.is_replay is True
    assert replay.fields == {"temperature_C": 20.5}
    # The callback saw both too.
    assert [e.is_replay for e in seen] == [False, True]


# --------------------------------------------------------------------------- #
# Fixture-driven smoke: every canned event fixture normalizes cleanly          #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "fixture_name, expected_key",
    [
        ("acurite_temp_humidity", "Acurite-606TX-42"),
        ("channel_only", "Prologue-TH-ch3"),
        ("doorbell_event", "Honeywell-Doorbell-7"),
        ("lightning", "AcuriteLightning-6045M-200-st2"),
        ("power_sensor", "EnergyMeter-2000-1234"),
        ("wind_rain_station", "Bresser-5in1-7"),
    ],
)
async def test_fixture_frames_emit_expected_device_key(
    make_client, load_fixture, fixture_name, expected_key
):
    """Each canned fixture's first frame emits an event with the expected key."""
    seen: list[NormalizedEvent] = []
    client = make_client(on_event=seen.append)
    frame = load_fixture(fixture_name)[0]

    client._handle_text_frame(json.dumps(frame))

    assert len(seen) == 1
    assert seen[0].device_key == expected_key
    assert "time" not in seen[0].fields  # time is always stripped from fields
