"""Standalone async WebSocket + HTTP ``/cmd`` client for one rtl_433 server.

Extracted from the rtl_433 integration's coordinator/base.py (Apache-2.0). This
reproduces that coordinator's *transport/protocol* half as a framework-free async
client: connect over WebSocket, parse JSON frames, ignore keep-alives/malformed
JSON, reconnect with capped exponential backoff, classify each event frame
against the reconnect replay, and drive the HTTP ``/cmd`` getters/setters that
surface the server's SDR/meta configuration, stats, and device identity.

Three couplings of the original coordinator become injectable seams here so the
client imports nothing from the integration framework:

- the shared session becomes an **injected** :class:`aiohttp.ClientSession`
  (or one the client creates and owns);
- the dispatcher fan-out becomes an **event callback** (``on_event``) and/or an
  ``async for event in client`` async-iterator that yields fully normalized,
  replay-classified :class:`~pyrtl_433.normalizer.NormalizedEvent` objects, plus
  a hub-changed callback (``on_hub_update``);
- the framework time helpers become the standard-library :mod:`datetime` via an
  injectable ``clock`` (defaulting to ``datetime.now(UTC)``).

Deliberately **out of scope** (framework policy in the original): the managed-SDR
desired-state store / adoption / enforcement, the availability watchdog, and
device-registration/discovery callbacks. The client only emits normalized events
and exposes the ``/cmd`` setter primitive (:meth:`Rtl433Client._send_cmd`) plus
the :mod:`pyrtl_433.sdr` command transforms, so a consumer can build any of that
policy on top.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
import dataclasses
from datetime import UTC, datetime, timedelta, tzinfo
import inspect
import json
import logging
from typing import Any

import aiohttp

from ._urls import build_cmd_url, build_ws_url, unwrap_result
from .autolevel import AUTO_LEVEL_SRC, parse_auto_level
from .normalizer import DEFAULT_SKIP_KEYS, NormalizedEvent, normalize
from .replay import classify_replay, parse_event_time

LOGGER = logging.getLogger(__name__)

# Backoff bounds for the reconnect loop (seconds). Starts at 1s, doubles on each
# consecutive failure, capped at 60s so the loop never spins hot.
_BACKOFF_MIN = 1.0
_BACKOFF_MAX = 60.0

# Timeout (seconds) for the short-lived connection attempt used by the
# reachability check.
_VALIDATE_TIMEOUT = 10.0

# Timeout (seconds) for each one-shot ``/cmd`` getter/setter HTTP request.
_GETTER_TIMEOUT = 10.0

# How often ``get_meta`` + ``get_stats`` are re-fetched over HTTP while connected,
# so the hub's SDR config and throughput stay live without depending on the
# streaming socket.
_REFRESH_INTERVAL = timedelta(seconds=60)

# Identity keys (besides ``model``) that mark a frame as a decoded-device event.
# Kept in sync with normalizer.IDENTITY_KEYS.
_EVENT_IDENTITY_KEYS = ("id", "channel", "subtype")

# Sentinel pushed onto the event queue by ``stop()`` so a consumer iterating with
# ``async for`` terminates cleanly instead of blocking forever on ``get()``.
_STOP = object()


class CannotConnect(RuntimeError):
    """Raised when the rtl_433 WebSocket endpoint cannot be reached."""


class Rtl433Client:
    """Async client owning the WebSocket connection to one rtl_433 server.

    Public API:
        ``start()`` / ``stop()`` — lifecycle (connect loop + periodic refresh).
        ``async for event in client`` — yield normalized, replay-classified events.
        ``refresh_meta()`` / ``refresh_stats()`` / ``refresh_dev_info()`` — HTTP
            getters that populate ``meta`` / ``stats`` / ``dev_info`` / ``dev_query``.
        ``_send_cmd(...)`` — the ``/cmd`` setter primitive.
        ``validate_connection(...)`` — a staticmethod reachability probe.

    Runtime state (read-only snapshots for a consumer):
        ``connected``: ``bool`` whether the socket is currently open.
        ``meta``: ``dict`` latest SDR/meta configuration (HTTP-sourced).
        ``stats``: ``dict`` latest server-stats payload (HTTP-sourced).
        ``dev_info``: ``dict`` the SDR's USB device label.
        ``dev_query``: ``str | None`` the ``-d`` selector rtl_433 opened.
        ``noise_level``: ``float | None`` estimated noise level in dB, parsed
            from the server's "Auto Level" log frames (socket-sourced; requires
            ``-Y autolevel`` and/or ``-M noise`` server-side).
        ``min_level``: ``float | None`` the auto-adjusted minimum detection
            level in dB (socket-sourced; requires ``-Y autolevel``).
    """

    def __init__(
        self,
        host: str,
        *,
        port: int = 8433,
        path: str = "/ws",
        secure: bool = False,
        session: aiohttp.ClientSession | None = None,
        skip_keys: set[str] | frozenset[str] | None = None,
        on_event: Callable[[NormalizedEvent], Any] | None = None,
        on_hub_update: Callable[[], Any] | None = None,
        on_log: Callable[[dict[str, Any]], Any] | None = None,
        clock: Callable[[], datetime] | None = None,
        event_tz: tzinfo | None = None,
    ) -> None:
        """Initialize the client with connection params and runtime state.

        ``session`` is the injected :class:`aiohttp.ClientSession`; pass ``None``
        to have the client create and own one (closed by :meth:`stop`). ``clock``
        supplies the current UTC instant (injectable for tests); it defaults to
        ``datetime.now(UTC)``. ``event_tz`` is the zone in which offset-less
        rtl_433 ``time`` stamps are interpreted for replay classification; pass
        the consumer's configured zone (e.g. Home Assistant's ``DEFAULT_TIME_ZONE``)
        so it does not depend on the host process time zone. Defaults to ``None``
        (system local zone). ``on_log`` receives every raw server log frame
        (``{"time", "src", "lvl", "msg"}``, rtl_433 >= 23.11) for consumers that
        want to surface the server's own log output; the "Auto Level" noise
        parsing below happens regardless of whether it is set.
        """
        self.host = host
        self.port = port
        self.path = path
        self.secure = secure

        # The injected session is never closed by this client; a client-created
        # one (``session is None``) is owned and closed on ``stop()``.
        self._session = session
        self._owns_session = session is None

        # Injected consumer seams (all optional). ``on_event`` / ``on_hub_update``
        # may be sync or return an awaitable (an async callback is scheduled).
        self._on_event = on_event
        self._on_hub_update = on_hub_update
        self._on_log = on_log
        self._clock: Callable[[], datetime] = (
            clock if clock is not None else (lambda: datetime.now(UTC))
        )
        # Zone for interpreting offset-less rtl_433 timestamps (``None`` -> system
        # local zone); threaded into :func:`parse_event_time` per frame.
        self._event_tz = event_tz

        # Keys dropped from measurement fields. ``time`` is always excluded: it is
        # read raw for the replay classification and must never reach a consumer as
        # a measurement field, regardless of the injected skip set.
        self._skip_keys: set[str] = (
            set(skip_keys) if skip_keys is not None else set(DEFAULT_SKIP_KEYS)
        )
        self._skip_keys.add("time")

        # Serializes ``/cmd`` issuance so a user write and a reconnect refresh can
        # never interleave requests to the same server.
        self._cmd_lock = asyncio.Lock()
        # Unbounded queue backing the ``async for`` interface; ``put_nowait`` keeps
        # the (sync) frame handlers from ever blocking on a slow consumer.
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._stop_event = asyncio.Event()

        # --- Hub-scoped runtime state (HTTP-sourced snapshots) ----------------
        self.connected = False
        self.meta: dict[str, Any] = {}
        self.stats: dict[str, Any] = {}
        self.dev_info: dict[str, Any] = {}
        self.dev_query: str | None = None
        # Receiver noise floor, parsed from "Auto Level" log frames on the socket
        # (the only place rtl_433 surfaces it — there is no structured getter).
        self.noise_level: float | None = None
        self.min_level: float | None = None
        # Getters currently returning malformed JSON, so the "invalid data" error
        # is logged once per command until it recovers (never floods on refresh).
        self._malformed_cmds: set[str] = set()

        # High-water mark of the maximum event ``time`` (UTC) ever parsed, used to
        # classify each frame against the reconnect replay. Spans reconnects so a
        # brief blip's re-sent buffer tail is recognised as already-seen.
        self._event_high_water: datetime | None = None
        # UTC time of the current successful connection (``None`` while
        # disconnected); anchors the pre-connection-backlog gate.
        self._connection_time: datetime | None = None

        # --- Internal lifecycle handles --------------------------------------
        self._task: asyncio.Task[None] | None = None
        self._refresh_task: asyncio.Task[None] | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        # In-flight tasks for awaitable (async) consumer callbacks.
        self._callback_tasks: set[asyncio.Task[Any]] = set()

    @property
    def ws_url(self) -> str:
        """Return the configured WebSocket URL for this server."""
        return build_ws_url(self.host, self.port, self.path, secure=self.secure)

    def _now(self) -> datetime:
        """Return the current instant via the injectable clock."""
        return self._clock()

    # ------------------------------------------------------------------ #
    # Lifecycle                                                          #
    # ------------------------------------------------------------------ #
    async def start(self) -> None:
        """Start the connect loop and the periodic refresh task."""
        if self._task is not None:
            return
        self._stop_event.clear()
        if self._session is None:
            self._session = aiohttp.ClientSession()
            self._owns_session = True
        self._task = asyncio.create_task(
            self._connect_loop(), name=f"pyrtl_433 ws {self.host}:{self.port}"
        )
        self._refresh_task = asyncio.create_task(
            self._refresh_loop(), name=f"pyrtl_433 refresh {self.host}:{self.port}"
        )
        LOGGER.debug("pyrtl_433 client started for %s", self.ws_url)

    async def stop(self) -> None:
        """Stop the loops, close the socket, and close an owned session."""
        self._stop_event.set()
        # Unblock any consumer waiting on the async iterator.
        self._queue.put_nowait(_STOP)

        if self._ws is not None and not self._ws.closed:
            await self._ws.close()

        for task in (self._task, self._refresh_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._task = None
        self._refresh_task = None

        for task in list(self._callback_tasks):
            task.cancel()
        self._callback_tasks.clear()

        self.connected = False

        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

        LOGGER.debug("pyrtl_433 client stopped for %s", self.ws_url)

    # ------------------------------------------------------------------ #
    # Event async-iterator                                               #
    # ------------------------------------------------------------------ #
    def __aiter__(self) -> Rtl433Client:
        """Return ``self`` as an async iterator over normalized events."""
        return self

    async def __anext__(self) -> NormalizedEvent:
        """Yield the next normalized event; stop cleanly once ``stop()`` ran."""
        item = await self._queue.get()
        if item is _STOP:
            raise StopAsyncIteration
        return item  # type: ignore[no-any-return]  # queue holds NormalizedEvent | _STOP

    # ------------------------------------------------------------------ #
    # Consumer-callback dispatch                                          #
    # ------------------------------------------------------------------ #
    def _invoke_callback(self, callback: Callable[..., Any] | None, *args: Any) -> None:
        """Call a consumer callback defensively; schedule it if it is awaitable.

        A sync callback runs inline (its exception is swallowed with a log so a
        bad hook cannot kill the connect loop); an async callback's coroutine is
        scheduled as a tracked task and its failure logged on completion.
        """
        if callback is None:
            return
        try:
            result = callback(*args)
        except Exception:
            LOGGER.exception("pyrtl_433 consumer callback raised")
            return
        if inspect.isawaitable(result):
            task = asyncio.ensure_future(result)
            self._callback_tasks.add(task)
            task.add_done_callback(self._on_callback_done)

    def _on_callback_done(self, task: asyncio.Task[Any]) -> None:
        """Reap a finished async-callback task and log any failure."""
        self._callback_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            LOGGER.error("pyrtl_433 async consumer callback failed: %s", exc)

    # ------------------------------------------------------------------ #
    # Connect / reconnect loop                                           #
    # ------------------------------------------------------------------ #
    async def _connect_loop(self) -> None:
        """Connect, stream frames, and reconnect with capped backoff on drop."""
        backoff = _BACKOFF_MIN

        while not self._stop_event.is_set():
            try:
                # self._session is set by start(); the loop only runs after that.
                async with self._session.ws_connect(  # type: ignore[union-attr]
                    self.ws_url, heartbeat=30
                ) as ws:
                    self._ws = ws
                    self.connected = True
                    self._connection_time = self._now()
                    backoff = _BACKOFF_MIN  # reset after a successful connect
                    self._invoke_callback(self._on_hub_update)
                    LOGGER.debug("pyrtl_433 connected to %s", self.ws_url)
                    # Anchor the replay classification: every frame's verdict in
                    # ``_process_event`` is judged against these two values.
                    LOGGER.debug(
                        "pyrtl_433 connection anchor for %s: connected_at=%s "
                        "replay_high_water=%s (frames at/below high-water, or "
                        "before connected_at, are suppressed as replays)",
                        self.ws_url,
                        self._connection_time.isoformat(),
                        self._event_high_water.isoformat()
                        if self._event_high_water is not None
                        else "none",
                    )
                    # Seed SDR/meta config, stats, and device identity over HTTP
                    # (never the socket). Each getter swallows its own failures, so
                    # a hidden ``/cmd`` (e.g. behind a proxy) cannot break the
                    # connection or the event stream.
                    await self.refresh_meta()
                    await self.refresh_stats()
                    await self.refresh_dev_info()
                    await self._read_frames(ws)
            except asyncio.CancelledError:
                raise
            except Exception as err:  # resilient: a bad frame/drop never kills loop
                LOGGER.debug("pyrtl_433 connection error for %s: %s", self.ws_url, err)
            finally:
                self._ws = None
                self.connected = False
                self._connection_time = None
                # The noise floor is a live measurement with no ``/cmd`` getter
                # to refresh on reconnect (unlike meta/stats), so clear it on
                # drop — otherwise the sensors would show a stale reading
                # indefinitely, e.g. after rtl_433 restarts with auto-level off.
                self.noise_level = None
                self.min_level = None
                self._invoke_callback(self._on_hub_update)

            if self._stop_event.is_set():
                break

            LOGGER.debug(
                "pyrtl_433 disconnected from %s; reconnecting in %.0fs",
                self.ws_url,
                backoff,
            )
            # Wait for the backoff window or an early stop, then retry.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
            backoff = min(backoff * 2, _BACKOFF_MAX)

    async def _refresh_loop(self) -> None:
        """Re-fetch meta + stats on the interval, only while connected."""
        while not self._stop_event.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=_REFRESH_INTERVAL.total_seconds()
                )
            if self._stop_event.is_set():
                break
            if self.connected:
                await self.refresh_meta()
                await self.refresh_stats()

    async def _read_frames(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """Iterate incoming frames until the socket closes or stop is set."""
        async for msg in ws:
            if self._stop_event.is_set():
                break
            if msg.type is aiohttp.WSMsgType.TEXT:
                self._handle_text_frame(msg.data)
            elif msg.type in (
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSING,
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.ERROR,
            ):
                break
            # PING/PONG/BINARY and anything else is ignored as keep-alive noise.

    def _handle_text_frame(self, data: str) -> None:
        """Parse one text frame and, if it is a valid event, process it."""
        text = data.strip() if isinstance(data, str) else data
        if not text:
            # Empty frames act as keep-alives.
            return
        try:
            event = json.loads(text)
        except (ValueError, TypeError) as err:
            LOGGER.debug("pyrtl_433 skipping malformed JSON frame: %s", err)
            return
        if not isinstance(event, dict):
            LOGGER.debug("pyrtl_433 skipping non-object frame: %r", text[:120])
            return
        self._classify_frame(event)

    def _classify_frame(self, event: dict[str, Any]) -> None:
        """Route a parsed frame by shape (event vs shutdown vs log vs ignored)."""
        is_event = event.get("model") is not None or any(
            event.get(key) is not None for key in _EVENT_IDENTITY_KEYS
        )
        if is_event:
            self._process_event(event)
        elif "shutdown" in event:
            self._handle_shutdown()
        elif "msg" in event and "lvl" in event:
            # A server log frame ({"time", "src", "lvl", "msg"}, rtl_433 >= 23.11).
            # Carries no model/identity keys, so it can never shadow an event.
            self._handle_log(event)
        # All other non-event frames (meta / state / result / error) are ignored:
        # meta and stats are sourced over HTTP, so nothing else needs handling here.

    def _process_event(self, event: dict[str, Any]) -> None:
        """Normalize an event, classify it (live vs replay), and emit it.

        Replays and stale gap events are still emitted (so a consumer can seed
        sensor values) but carry ``is_replay=True`` so the consumer can suppress
        re-firing on them. The classification is delegated to
        :func:`~pyrtl_433.replay.classify_replay`; this method applies its verdict
        to the high-water mark and stamps the event object.
        """
        normalized = normalize(event, self._skip_keys)

        now = self._now()

        # Read the raw ``time`` independently of ``normalize`` (which drops it) and
        # classify the frame into live / replay / stale gap / pre-connection backlog.
        event_time = parse_event_time(event.get("time"), default_tz=self._event_tz)
        verdict = classify_replay(
            event_time,
            now,
            high_water=self._event_high_water,
            connection_time=self._connection_time,
        )
        if verdict.new_high_water is not None:
            self._event_high_water = verdict.new_high_water

        # Carry the classification on the event object (the emission carrier).
        normalized = dataclasses.replace(
            normalized, is_replay=verdict.is_replay, event_time=event_time
        )

        LOGGER.debug(
            "pyrtl_433 RX %s fields=%s time=%s -> %s",
            normalized.device_key,
            normalized.fields,
            event_time.isoformat() if event_time is not None else "none",
            verdict.label,
        )

        self._emit_event(normalized)

    def _emit_event(self, normalized: NormalizedEvent) -> None:
        """Publish a normalized event to the queue and the ``on_event`` callback."""
        self._queue.put_nowait(normalized)
        self._invoke_callback(self._on_event, normalized)

    def _handle_shutdown(self) -> None:
        """Handle a ``{"shutdown": ...}`` frame: flip connectivity off."""
        if self.connected:
            LOGGER.debug("pyrtl_433 server announced shutdown for %s", self.ws_url)
        self.connected = False
        self._invoke_callback(self._on_hub_update)

    def _handle_log(self, event: dict[str, Any]) -> None:
        """Handle a server log frame: forward it raw, and mine "Auto Level" data.

        Every log frame goes to the optional ``on_log`` callback verbatim. Frames
        from the pulse detector's auto-level feature (``src == "Auto Level"``)
        are additionally parsed (:func:`~pyrtl_433.autolevel.parse_auto_level`)
        into the ``noise_level`` / ``min_level`` snapshots — the only source of
        the receiver's noise floor rtl_433 offers. ``on_hub_update`` fires only
        when a parsed value actually changed; an unrecognized message updates
        nothing (never fails, never stores a misread value).
        """
        msg = event.get("msg")
        self._invoke_callback(self._on_log, event)
        if event.get("src") != AUTO_LEVEL_SRC or not isinstance(msg, str):
            return
        reading = parse_auto_level(msg)
        if reading is None:
            LOGGER.debug("pyrtl_433 unrecognized Auto Level message: %r", msg)
            return
        changed = reading.noise_db != self.noise_level
        self.noise_level = reading.noise_db
        if reading.min_level_db is not None:
            changed = changed or reading.min_level_db != self.min_level
            self.min_level = reading.min_level_db
        if changed:
            self._invoke_callback(self._on_hub_update)

    # ------------------------------------------------------------------ #
    # HTTP ``/cmd`` transport (SDR/meta config + server stats)           #
    # ------------------------------------------------------------------ #
    async def _fetch_cmd(self, command: str) -> Any | None:
        """GET one ``/cmd`` getter; return parsed JSON or None on any failure.

        Targets the server root (never the WS ``path``). Any HTTP/parse error is
        caught and logged at debug so a single getter — or a proxy that hides
        ``/cmd`` — can never raise into the connect loop or the refresh loop.
        """
        url = build_cmd_url(self.host, self.port, secure=self.secure)
        try:
            async with self._session.get(  # type: ignore[union-attr]
                url,
                params={"cmd": command},
                timeout=aiohttp.ClientTimeout(total=_GETTER_TIMEOUT),
            ) as resp:
                resp.raise_for_status()
                try:
                    # rtl_433 may not set a strict ``application/json``
                    # content-type for scalar getters, so do not require one.
                    payload = await resp.json(content_type=None)
                except ValueError as err:
                    # Reachable and 2xx, but the body is not valid JSON — a genuine
                    # server fault (the known cause is a truncated ``get_stats``).
                    # Surface it at error level, but only once per command until it
                    # recovers so the periodic refresh tick can't flood the log.
                    if command not in self._malformed_cmds:
                        self._malformed_cmds.add(command)
                        LOGGER.error(
                            "pyrtl_433 server returned invalid data for '%s' "
                            "(at %s); the related hub sensors will not update: %s",
                            command,
                            url,
                            err,
                        )
                    return None
                self._malformed_cmds.discard(command)
                return payload
        except Exception as err:  # getters must never kill the loop
            LOGGER.debug("pyrtl_433 getter %s failed at %s: %s", command, url, err)
            return None

    async def _send_cmd(
        self, command: str, *, val: int | None = None, arg: str | None = None
    ) -> bool:
        """Issue one setter ``/cmd`` over HTTP; return ``True`` on success.

        Mirrors :meth:`_fetch_cmd` (server-root URL, never the WS ``path``) but
        adds the ``val``/``arg`` query params and serializes the send under
        ``_cmd_lock`` so two writes can never interleave. ``val`` is stringified as
        an integer; ``arg`` is sent verbatim — including the empty string, which is
        the gain "auto" sentinel (so the gain command must always pass ``arg``,
        never omit it). Any HTTP/parse error is caught, logged at debug, and
        returns ``False``. Commands go only over ``/cmd`` — never the socket.
        """
        url = build_cmd_url(self.host, self.port, secure=self.secure)
        params: dict[str, str] = {"cmd": command}
        if val is not None:
            params["val"] = str(int(val))
        if arg is not None:
            params["arg"] = arg
        async with self._cmd_lock:
            try:
                async with self._session.get(  # type: ignore[union-attr]
                    url,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=_GETTER_TIMEOUT),
                ) as resp:
                    resp.raise_for_status()
                return True
            except Exception as err:  # never kill the loop
                LOGGER.debug("pyrtl_433 /cmd %s failed at %s: %s", command, url, err)
                return False

    async def refresh_meta(self) -> None:
        """Fetch ``get_meta`` + ``get_gain`` + ``get_ppm_error`` into ``meta``."""
        meta = unwrap_result(await self._fetch_cmd("get_meta"))
        gain = unwrap_result(await self._fetch_cmd("get_gain"))
        ppm = unwrap_result(await self._fetch_cmd("get_ppm_error"))

        new_meta: dict[str, Any] = {}
        if isinstance(meta, dict):
            for key in (
                "center_frequency",
                "samp_rate",
                "conversion_mode",
                "frequencies",
                "hop_times",
            ):
                if key in meta:
                    new_meta[key] = meta[key]
            hop_times = meta.get("hop_times")
            if isinstance(hop_times, list) and hop_times:
                new_meta["hop_interval"] = hop_times[0]
        if isinstance(gain, str):
            new_meta["gain"] = gain
        if isinstance(ppm, int) and not isinstance(ppm, bool):
            new_meta["ppm_error"] = ppm

        if new_meta:
            self.meta = {**self.meta, **new_meta}
            self._invoke_callback(self._on_hub_update)

    async def refresh_stats(self) -> None:
        """Fetch ``get_stats`` into ``stats``."""
        stats = unwrap_result(await self._fetch_cmd("get_stats"))
        if isinstance(stats, dict):
            self.stats = stats
            self._invoke_callback(self._on_hub_update)

    async def refresh_dev_info(self) -> None:
        """Fetch the SDR device identity into ``dev_info`` / ``dev_query``.

        ``get_dev_info`` is the librtlsdr USB device label as a JSON object
        (``{"vendor", "product", "serial"}``); ``get_dev_query`` is the ``-d``
        selector rtl_433 opened. Both are static per dongle, so this only runs on
        (re)connect. Either may be empty/unset when no SDR device is open (e.g.
        ``-D manual``); the stored value is then left untouched. ``on_hub_update``
        fires only when the identity changes.
        """
        info = unwrap_result(await self._fetch_cmd("get_dev_info"))
        query = unwrap_result(await self._fetch_cmd("get_dev_query"))

        # Over ``/cmd`` the JSON object is embedded directly, but accept a JSON
        # string too (WS framing / a proxy) so the parse is transport-agnostic.
        if isinstance(info, str):
            try:
                info = json.loads(info)
            except ValueError:
                info = None

        changed = False
        if isinstance(info, dict) and info and info != self.dev_info:
            self.dev_info = info
            changed = True
        if isinstance(query, str) and query and query != self.dev_query:
            self.dev_query = query
            changed = True

        if changed:
            self._invoke_callback(self._on_hub_update)

    # ------------------------------------------------------------------ #
    # Connectivity check                                                 #
    # ------------------------------------------------------------------ #
    @staticmethod
    async def validate_connection(
        session: aiohttp.ClientSession,
        host: str,
        port: int,
        path: str,
        *,
        secure: bool = False,
    ) -> bool:
        """Attempt a short-lived WebSocket connection to verify reachability.

        Returns ``True`` on success and closes immediately (no side effects).
        Raises :class:`CannotConnect` if the endpoint cannot be reached.
        """
        url = build_ws_url(host, port, path, secure=secure)
        try:
            # Bound the handshake with asyncio.timeout: aiohttp's ws_connect
            # ``timeout`` parameter is a ``ClientWSTimeout`` (receive/close), not a
            # whole-connect deadline, so wrapping is the correct way to cap it.
            async with asyncio.timeout(_VALIDATE_TIMEOUT):
                ws = await session.ws_connect(url)
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise CannotConnect(f"Cannot connect to {url}: {err}") from err
        else:
            await ws.close()
            return True
