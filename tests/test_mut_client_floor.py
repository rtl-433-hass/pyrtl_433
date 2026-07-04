"""Mutation-FLOOR tests for :class:`pyrtl_433.client.Rtl433Client`.

The hardest paths to pin: the reconnect backoff schedule, the replay-classification
boundaries *as observed through the client* (live / replay / stale-gap / backlog +
high-water advance), the ``/cmd`` lock serialization and malformed-JSON error
dedup, the ``start``/``stop`` lifecycle (idempotency, session ownership, async-for
unblock), the sync/async consumer-callback dispatch, and the ``validate_connection``
URL/timeout/exception-message details. These are the mutant-dense regions the
parent's ``coordinator/base.py`` floor concentrates on.

Backoff timing is asserted deterministically by patching ``asyncio.wait_for`` in
the client module to record the requested delay and never actually wait.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import inspect
import logging

import aiohttp
import pytest

from pyrtl_433.client import CannotConnect, Rtl433Client

HOST = "rtl433.local"


# --------------------------------------------------------------------------- #
# Reconnect backoff: doubles 1->2->...->60 and caps at 60; resets on success   #
# --------------------------------------------------------------------------- #
def _patch_wait_for(monkeypatch, client, recorded, stop_after):
    """Patch ``asyncio.wait_for`` to record the timeout and stop after N calls."""

    async def fake_wait_for(awaitable, timeout):
        recorded.append(timeout)
        # Close the un-awaited ``stop_event.wait()`` coroutine (warnings-clean).
        if inspect.iscoroutine(awaitable):
            awaitable.close()
        if len(recorded) >= stop_after:
            client._stop_event.set()
        raise TimeoutError  # suppressed by the loop's contextlib.suppress

    monkeypatch.setattr("pyrtl_433.client.asyncio.wait_for", fake_wait_for)


async def test_backoff_doubles_and_caps_at_60(make_client, fake_session, monkeypatch):
    """Every failed connect doubles the delay 1->2->4...->60, then holds at 60."""
    client = make_client()
    fake_session.default_ws_outcome = aiohttp.ClientError("down")  # all connects fail
    recorded: list[float] = []
    _patch_wait_for(monkeypatch, client, recorded, stop_after=9)

    await client._connect_loop()

    assert recorded == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0, 60.0]


async def test_backoff_resets_to_one_after_successful_connect(
    make_client, fake_session, monkeypatch
):
    """A successful connect resets the backoff, so the next failure waits 1s again."""
    client = make_client()
    # fail, fail, succeed (empty stream), then it would fail again but we stop.
    fake_session.ws_outcomes.extend(
        [aiohttp.ClientError("d1"), aiohttp.ClientError("d2"), []]
    )
    fake_session.default_ws_outcome = aiohttp.ClientError("d3")
    recorded: list[float] = []
    _patch_wait_for(monkeypatch, client, recorded, stop_after=3)

    await client._connect_loop()

    # 1 (after 1st fail), 2 (after 2nd fail), then 1 again (reset by the success).
    assert recorded == [1.0, 2.0, 1.0]


async def test_connect_loop_breaks_when_stop_set_during_session(
    make_client, fake_session
):
    """Stop requested mid-session: the loop breaks out instead of scheduling a retry."""
    # The on-connect hub-update callback trips the stop event, so once the (empty)
    # session ends the loop takes the post-session stop check and exits with no
    # backoff wait — no reconnect is attempted.
    client = make_client(on_hub_update=lambda: client._stop_event.set())
    fake_session.ws_outcomes.append([])  # a single successful, empty connect
    fake_session.default_ws_outcome = aiohttp.ClientError("should-not-reconnect")

    await client._connect_loop()

    assert client.connected is False
    # Exactly one connect attempt: the loop broke rather than retrying.
    assert len(fake_session.ws_connect_calls) == 1


async def test_successful_connect_sets_connected_and_connection_time(
    make_client, fake_session, fake_clock, monkeypatch
):
    """On connect the client flips connected on and anchors the connection time."""
    now = datetime(2026, 5, 25, 10, 0, 0, tzinfo=UTC)
    fake_clock.set(now)
    states: list[bool] = []
    client = make_client(on_hub_update=lambda: states.append(client.connected))
    fake_session.ws_outcomes.append([])  # one empty successful connect
    fake_session.default_ws_outcome = aiohttp.ClientError("down")
    recorded: list[float] = []
    _patch_wait_for(monkeypatch, client, recorded, stop_after=1)

    await client._connect_loop()

    # on_hub_update fired at least twice: once connected (True), once on drop (False).
    assert True in states
    assert states[-1] is False  # finally block flips it off on disconnect
    assert client.connected is False  # loop exited disconnected
    assert client._connection_time is None  # cleared on disconnect (not "" etc.)
    # The socket was opened with the 30s application-level heartbeat.
    assert fake_session.ws_connect_calls[0].kwargs["heartbeat"] == 30
    assert fake_session.ws_connect_calls[0].url == "ws://rtl433.local:8433/ws"


# --------------------------------------------------------------------------- #
# Replay classification observed through the client (_process_event)            #
# --------------------------------------------------------------------------- #
def _event(time_str):
    return {"time": time_str, "model": "Foo", "id": 1, "temperature_C": 1.0}


async def test_process_event_live_advances_high_water(make_client, fake_clock):
    """A fresh live frame is emitted (is_replay=False) and advances the mark to it."""
    now = datetime(2026, 5, 25, 10, 0, 0, tzinfo=UTC)
    fake_clock.set(now)
    seen = []
    client = make_client(on_event=seen.append)

    client._process_event(_event("2026-05-25T10:00:00Z"))

    assert seen[0].is_replay is False
    assert seen[0].event_time == now
    assert client._event_high_water == now
    # It also reached the async-iterator queue.
    assert client._queue.get_nowait() is seen[0]


async def test_process_event_replay_leaves_high_water(make_client, fake_clock):
    """A frame at/below the mark is a replay (is_replay=True); the mark is unchanged."""
    fake_clock.set(datetime(2026, 5, 25, 10, 0, 5, tzinfo=UTC))
    hw = datetime(2026, 5, 25, 10, 0, 0, tzinfo=UTC)
    seen = []
    client = make_client(on_event=seen.append)
    client._event_high_water = hw

    client._process_event(_event("2026-05-25T10:00:00Z"))  # == high water

    assert seen[0].is_replay is True
    assert client._event_high_water == hw  # unchanged


async def test_process_event_stale_gap_advances_high_water(make_client, fake_clock):
    """A never-seen but old frame is a stale gap (is_replay=True) and advances the mark."""
    fake_clock.set(datetime(2026, 5, 25, 10, 5, 0, tzinfo=UTC))  # 5 min later
    seen = []
    client = make_client(on_event=seen.append)

    client._process_event(_event("2026-05-25T10:00:00Z"))

    assert seen[0].is_replay is True
    assert client._event_high_water == datetime(2026, 5, 25, 10, 0, 0, tzinfo=UTC)


async def test_process_event_backlog_is_replay(make_client, fake_clock):
    """A recent frame stamped before this connection is a replayed backlog frame."""
    now = datetime(2026, 5, 25, 10, 0, 11, tzinfo=UTC)
    fake_clock.set(now)
    seen = []
    client = make_client(on_event=seen.append)
    client._connection_time = now  # connected at 10:00:11
    # Event at 10:00:00 is 11s old (< 30s, recent) but > 5s before the connection.
    client._process_event(_event("2026-05-25T10:00:00Z"))

    assert seen[0].is_replay is True
    assert client._event_high_water == datetime(2026, 5, 25, 10, 0, 0, tzinfo=UTC)


async def test_process_event_future_stamp_clamped_to_now(make_client, fake_clock):
    """A future-stamped live frame fires live but clamps the mark to now, not ahead."""
    now = datetime(2026, 5, 25, 10, 0, 0, tzinfo=UTC)
    fake_clock.set(now)
    seen = []
    client = make_client(on_event=seen.append)

    client._process_event(_event("2026-05-25T11:00:00Z"))  # 1h in the future

    assert seen[0].is_replay is False
    assert client._event_high_water == now  # clamped to now, not the future stamp


async def test_handle_text_frame_strips_whitespace_then_processes(
    make_client, fake_clock
):
    """A frame padded with whitespace is stripped and still processed as an event."""
    fake_clock.set(datetime(2026, 5, 25, 10, 0, 0, tzinfo=UTC))
    seen = []
    client = make_client(on_event=seen.append)

    client._handle_text_frame(
        '   {"time": "2026-05-25T10:00:00Z", "model": "X", "id": 1, "t": 1}   '
    )

    assert len(seen) == 1
    assert seen[0].device_key == "X-1"


# --------------------------------------------------------------------------- #
# /cmd lock serialization                                                       #
# --------------------------------------------------------------------------- #
async def test_send_cmd_serialized_under_cmd_lock(make_client, fake_session):
    """_send_cmd waits on _cmd_lock: while it is held, no request is issued."""
    client = make_client()
    await client._cmd_lock.acquire()

    task = asyncio.create_task(client._send_cmd("gain", arg=""))
    for _ in range(3):
        await asyncio.sleep(0)  # let the task reach the lock and block

    assert not task.done()  # blocked on the held lock
    assert fake_session.get_calls == []  # no /cmd request escaped the lock

    client._cmd_lock.release()
    assert await asyncio.wait_for(task, timeout=1) is True
    assert len(fake_session.get_calls) == 1  # the single serialized request


# --------------------------------------------------------------------------- #
# _fetch_cmd: malformed JSON logged once per command, then recovers            #
# --------------------------------------------------------------------------- #
async def test_malformed_json_logged_once_then_recovers(
    make_client, fake_session, make_response, caplog
):
    """A 2xx-but-non-JSON body logs an error once per command and dedups until it recovers."""
    client = make_client()
    fake_session.cmd_responses["get_stats"] = make_response(
        json_exc=ValueError("truncated body")
    )

    with caplog.at_level(logging.ERROR, logger="pyrtl_433.client"):
        assert await client._fetch_cmd("get_stats") is None
        assert "get_stats" in client._malformed_cmds  # remembered
        # Second call while still malformed: no new error log (dedup).
        assert await client._fetch_cmd("get_stats") is None

    errors = [
        r
        for r in caplog.records
        if r.levelno == logging.ERROR and r.name == "pyrtl_433.client"
    ]
    assert len(errors) == 1  # logged exactly once, not per refresh tick
    # The operator-facing message names the failing command and the impact.
    message = errors[0].getMessage()
    assert "get_stats" in message
    assert "invalid data" in message

    # Recovery: a valid body clears the dedup flag and returns the payload.
    fake_session.cmd_responses["get_stats"] = {"result": {"frames": 1}}
    assert await client._fetch_cmd("get_stats") == {"result": {"frames": 1}}
    assert "get_stats" not in client._malformed_cmds


async def test_fetch_cmd_targets_cmd_root_not_ws_path(make_client, fake_session):
    """_fetch_cmd always hits /cmd at the server root, never the WS path."""
    client = make_client(path="/some/stream/socket")
    fake_session.cmd_responses["get_meta"] = {"result": {}}

    await client._fetch_cmd("get_meta")

    assert fake_session.get_calls[-1].url == "http://rtl433.local:8433/cmd"


# --------------------------------------------------------------------------- #
# start / stop lifecycle                                                        #
# --------------------------------------------------------------------------- #
async def test_start_is_idempotent(make_client, fake_session):
    """A second start() is a no-op: the same connect/refresh tasks are kept."""
    client = make_client()
    fake_session.default_ws_outcome = aiohttp.ClientError("down")

    await client.start()
    task1 = client._task
    refresh1 = client._refresh_task
    assert task1 is not None
    assert refresh1 is not None  # both the connect and refresh tasks are created

    await client.start()  # idempotent
    assert client._task is task1
    assert client._refresh_task is refresh1

    await client.stop()


async def test_stop_closes_owned_session_not_injected(make_client, fake_session):
    """stop() closes a client-owned session but never an injected one."""
    # Injected session: stop must NOT close it (the caller owns its lifecycle).
    injected = make_client()  # wired to the injected fake_session
    await injected.stop()
    assert fake_session.closed is False

    # Owned session: constructed with session=None, so the client owns + closes it.
    owned = make_client(session=None)
    owned_session = type(fake_session)()  # a second fake, owned by the client
    owned._session = owned_session

    await owned.stop()
    assert owned_session.closed is True
    assert owned_session.close_calls == 1


async def test_start_creates_and_owns_session_when_none(
    make_client, fake_session, monkeypatch
):
    """With session=None, start() creates a session it owns and closes on stop()."""
    fake_session.default_ws_outcome = aiohttp.ClientError("down")
    monkeypatch.setattr(
        "pyrtl_433.client.aiohttp.ClientSession", lambda *a, **k: fake_session
    )
    client = make_client(session=None)
    assert client._owns_session is True

    await client.start()
    assert client._session is fake_session  # the created session is adopted

    await client.stop()
    assert fake_session.closed is True  # an owned session is closed on stop


async def test_refresh_loop_refreshes_only_while_connected(
    make_client, fake_session, monkeypatch
):
    """The periodic loop re-fetches meta+stats on the 60s tick, only when connected."""
    client = make_client()
    fake_session.cmd_responses.update(
        {
            "get_meta": {"result": {"center_frequency": 1}},
            "get_gain": {"result": None},
            "get_ppm_error": {"result": None},
            "get_stats": {"result": {"frames": 2}},
        }
    )
    ticks: list[float] = []

    async def fake_wait_for(awaitable, timeout):
        ticks.append(timeout)
        if inspect.iscoroutine(awaitable):
            awaitable.close()
        if len(ticks) >= 2:  # let one full tick refresh, then stop
            client._stop_event.set()
        raise TimeoutError

    monkeypatch.setattr("pyrtl_433.client.asyncio.wait_for", fake_wait_for)

    # Connected -> the tick refreshes meta + stats.
    client.connected = True
    await client._refresh_loop()

    assert ticks[0] == 60.0  # the documented refresh interval
    assert client.meta["center_frequency"] == 1
    assert client.stats == {"frames": 2}


async def test_refresh_loop_skips_refresh_when_disconnected(
    make_client, fake_session, monkeypatch
):
    """While disconnected the tick performs no /cmd refresh (kills the guard drop)."""
    client = make_client()
    fake_session.cmd_responses["get_meta"] = {"result": {"center_frequency": 9}}
    ticks: list[float] = []

    async def fake_wait_for(awaitable, timeout):
        ticks.append(timeout)
        if inspect.iscoroutine(awaitable):
            awaitable.close()
        client._stop_event.set()
        raise TimeoutError

    monkeypatch.setattr("pyrtl_433.client.asyncio.wait_for", fake_wait_for)

    client.connected = False
    await client._refresh_loop()

    assert client.meta == {}  # no refresh happened while disconnected
    assert fake_session.get_calls == []


async def test_stop_unblocks_async_iterator(make_client):
    """stop() pushes the stop sentinel so a waiting async-for terminates cleanly."""
    client = make_client()

    await client.stop()

    with pytest.raises(StopAsyncIteration):
        await client.__anext__()


async def test_stop_closes_open_ws(make_client, make_ws):
    """stop() closes a currently-open socket."""
    client = make_client()
    ws = make_ws([])
    client._ws = ws  # simulate an open connection

    await client.stop()

    assert ws.closed is True


async def test_stop_while_connected_cancels_connect_loop(
    make_client, fake_session, make_ws
):
    """stop() while connected cancels the loop parked in _read_frames and closes the ws."""
    blocking_ws = make_ws([], blocking=True)  # connects, then blocks on the read
    fake_session.ws_outcomes.append(blocking_ws)
    fake_session.default_ws_outcome = aiohttp.ClientError("down")

    connected = asyncio.Event()

    def on_hub_update():
        if client.connected:
            connected.set()

    client = make_client(on_hub_update=on_hub_update)

    await client.start()
    await asyncio.wait_for(connected.wait(), timeout=1)
    assert client.connected is True

    await client.stop()  # cancels the connect task blocked in the read loop

    assert client.connected is False
    assert blocking_ws.closed is True


async def test_stop_cancels_pending_async_callback_tasks(make_client):
    """A still-running async callback is cancelled (not leaked) by stop()."""
    started = asyncio.Event()

    async def slow_callback():
        started.set()
        await asyncio.sleep(3600)  # never completes on its own

    client = make_client(on_hub_update=slow_callback)
    client._handle_shutdown()  # schedules slow_callback as a tracked task

    await asyncio.wait_for(started.wait(), timeout=1)
    assert len(client._callback_tasks) == 1

    await client.stop()
    # Let the cancellation settle so the reaper (which returns early for a
    # cancelled task) runs and no pending task leaks past the test.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert client._callback_tasks == set()


# --------------------------------------------------------------------------- #
# Consumer-callback dispatch: sync inline, async scheduled, failures logged     #
# --------------------------------------------------------------------------- #
async def test_async_on_event_callback_scheduled_and_runs(make_client, fake_clock):
    """An awaitable on_event callback is scheduled as a tracked task and runs."""
    fake_clock.set(datetime(2026, 5, 25, 10, 0, 0, tzinfo=UTC))
    seen = []

    async def on_event(evt):
        seen.append(evt)

    client = make_client(on_event=on_event)
    client._handle_text_frame(
        '{"time": "2026-05-25T10:00:00Z", "model": "X", "id": 1, "t": 1}'
    )

    assert len(client._callback_tasks) == 1  # scheduled, not run inline
    await asyncio.gather(*client._callback_tasks)
    await asyncio.sleep(0)  # let the done-callback reap the task
    assert len(seen) == 1
    # The finished task is discarded from the tracking set (no leak).
    assert client._callback_tasks == set()


async def test_sync_callback_exception_is_swallowed(make_client):
    """A raising sync callback is caught so it cannot kill the frame path."""

    def boom():
        raise RuntimeError("bad hook")

    client = make_client(on_hub_update=boom)

    client._handle_shutdown()  # must not raise

    assert client.connected is False


async def test_async_callback_failure_is_logged(make_client, caplog):
    """A failing awaitable callback is reaped and its failure logged."""

    async def boom():
        raise RuntimeError("async bad hook")

    client = make_client(on_hub_update=boom)

    with caplog.at_level(logging.ERROR, logger="pyrtl_433.client"):
        client._handle_shutdown()
        await asyncio.gather(*client._callback_tasks, return_exceptions=True)
        await asyncio.sleep(0)  # let the done-callback run

    assert "async consumer callback failed" in caplog.text


# --------------------------------------------------------------------------- #
# validate_connection: URL / secure / timeout / exception detail               #
# --------------------------------------------------------------------------- #
async def test_validate_connection_insecure_uses_ws_url(fake_session):
    """secure=False (the default) probes a ws:// URL."""
    fake_session.default_ws_outcome = []

    await Rtl433Client.validate_connection(fake_session, HOST, 8433, "/ws")

    assert fake_session.ws_connect_calls[-1].url.startswith("ws://")
    assert not fake_session.ws_connect_calls[-1].url.startswith("wss://")


async def test_validate_connection_passes_timeout(fake_session):
    """The probe carries the 10s validate timeout (kills a dropped-timeout mutant)."""
    fake_session.default_ws_outcome = []

    await Rtl433Client.validate_connection(fake_session, HOST, 8433, "/ws")

    kwargs = fake_session.ws_connect_calls[-1].kwargs
    assert "timeout" in kwargs
    assert kwargs["timeout"].total == 10.0


async def test_validate_connection_error_message_contains_url(fake_session):
    """The CannotConnect message embeds the target URL (aid to the operator)."""
    fake_session.default_ws_outcome = aiohttp.ClientError("refused")

    with pytest.raises(CannotConnect) as excinfo:
        await Rtl433Client.validate_connection(fake_session, HOST, 8433, "/ws")

    assert "ws://rtl433.local:8433/ws" in str(excinfo.value)


async def test_validate_connection_unexpected_error_propagates(fake_session):
    """An error outside (ClientError, TimeoutError, OSError) is NOT swallowed."""
    fake_session.default_ws_outcome = ValueError("weird, not a connect error")

    with pytest.raises(ValueError):
        await Rtl433Client.validate_connection(fake_session, HOST, 8433, "/ws")
