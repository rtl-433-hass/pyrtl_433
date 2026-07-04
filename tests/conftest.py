"""Shared test doubles and fixtures for the ``Rtl433Client`` suite.

The client talks to a server over exactly two aiohttp surfaces:

- ``session.ws_connect(url, **kw)`` — used two ways: as an ``async with`` context
  manager in the connect loop, and *awaited directly* in ``validate_connection``.
  The real aiohttp return value supports both, so :class:`_WSConnectContext`
  implements both ``__aenter__``/``__aexit__`` and ``__await__``.
- ``session.get(url, params=..., timeout=...)`` — an ``async with`` context
  manager yielding a response with a sync ``raise_for_status()`` and an awaitable
  ``json(content_type=None)``.

Everything here is a hand-rolled double (no ``unittest.mock`` autospec) so a test
can script exact frame sequences, per-command ``/cmd`` payloads, connect
failures, malformed JSON, and non-200 responses, and then assert on the exact
requests the client issued. All fakes are warnings-clean under
``filterwarnings=["error"]``: no coroutine is created unless it is awaited.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import aiohttp
import pytest

from pyrtl_433.client import Rtl433Client

_FIXTURES = Path(__file__).parent / "fixtures"


# --------------------------------------------------------------------------- #
# Injectable clock                                                            #
# --------------------------------------------------------------------------- #
class FakeClock:
    """A callable clock returning a controllable UTC instant.

    ``client(clock=fake_clock)`` uses this for ``_now()``; a test can pin ``now``
    and ``tick`` it forward to place crafted event ``time`` values on either side
    of the replay thresholds deterministically.
    """

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 5, 25, 10, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self._now

    def set(self, when: datetime) -> None:
        self._now = when

    def tick(self, seconds: float) -> datetime:
        self._now = self._now + timedelta(seconds=seconds)
        return self._now


# --------------------------------------------------------------------------- #
# Fake HTTP response + GET context                                            #
# --------------------------------------------------------------------------- #
class FakeResponse:
    """A scriptable ``/cmd`` HTTP response.

    ``raise_for_status`` raises a realistic :class:`aiohttp.ClientResponseError`
    for any ``status >= 400`` (matching aiohttp). ``json`` returns ``json_data``
    or raises ``json_exc`` (used to simulate a truncated / non-JSON body on a 2xx
    response — the malformed-getter path).
    """

    def __init__(
        self,
        *,
        json_data: Any = None,
        json_exc: BaseException | None = None,
        status: int = 200,
        url: str = "http://rtl433.local:8433/cmd",
    ) -> None:
        self.json_data = json_data
        self.json_exc = json_exc
        self.status = status
        self.url = url
        self.raise_for_status_calls = 0
        self.json_calls = 0

    def raise_for_status(self) -> None:
        self.raise_for_status_calls += 1
        if self.status >= 400:
            raise aiohttp.ClientResponseError(
                SimpleNamespace(real_url=self.url),
                (),
                status=self.status,
                message=f"HTTP {self.status}",
            )

    async def json(self, content_type: str | None = "application/json") -> Any:
        self.json_calls += 1
        if self.json_exc is not None:
            raise self.json_exc
        return self.json_data


class _GetContext:
    """Async context manager returned by ``FakeSession.get``."""

    def __init__(self, session: FakeSession, params: dict[str, str]) -> None:
        self._session = session
        self._params = params

    async def __aenter__(self) -> FakeResponse:
        return self._session._resolve_response(self._params)

    async def __aexit__(self, *exc: object) -> bool:
        return False


# --------------------------------------------------------------------------- #
# Fake WebSocket + connect context                                            #
# --------------------------------------------------------------------------- #
class FakeWS:
    """A fake WebSocket that replays a caller-supplied list of ``WSMessage``s.

    With ``blocking=True`` the iterator, once its scripted messages are drained,
    parks on a never-set gate instead of ending — modelling a live connection that
    is only broken by cancellation (used to drive the graceful-shutdown path where
    ``stop()`` cancels the connect loop while it is reading frames).
    """

    def __init__(
        self, messages: Iterable[aiohttp.WSMessage] = (), *, blocking: bool = False
    ) -> None:
        self._messages = deque(messages)
        self.closed = False
        self.close_calls = 0
        self._blocking = blocking
        self._gate: asyncio.Event | None = None

    def __aiter__(self) -> FakeWS:
        return self

    async def __anext__(self) -> aiohttp.WSMessage:
        if self._messages:
            return self._messages.popleft()
        if self._blocking and not self.closed:
            if self._gate is None:
                self._gate = asyncio.Event()
            await self._gate.wait()  # unblocked only by cancellation
        raise StopAsyncIteration

    async def close(self) -> bool:
        self.closed = True
        self.close_calls += 1
        return True


def _materialize(outcome: Any, session: FakeSession) -> FakeWS:
    """Resolve a scripted ws_connect outcome to a ``FakeWS`` (or raise).

    ``outcome`` may be an exception instance (raised to simulate a connect
    failure), an existing ``FakeWS``, or an iterable of ``WSMessage``s (wrapped in
    a fresh ``FakeWS``). ``None`` yields an immediately-empty socket.
    """
    if isinstance(outcome, BaseException):
        raise outcome
    if isinstance(outcome, FakeWS):
        ws = outcome
    else:
        ws = FakeWS(list(outcome) if outcome else [])
    session.ws_instances.append(ws)
    return ws


class _WSConnectContext:
    """Return value of ``FakeSession.ws_connect``.

    Supports both usage patterns the client relies on: ``async with`` (connect
    loop) and a direct ``await`` (``validate_connection``).
    """

    def __init__(self, session: FakeSession, outcome: Any) -> None:
        self._session = session
        self._outcome = outcome
        self._ws: FakeWS | None = None

    # Direct-await form: ``ws = await session.ws_connect(url, timeout=...)``.
    def __await__(self):
        return self._await_impl().__await__()

    async def _await_impl(self) -> FakeWS:
        return _materialize(self._outcome, self._session)

    # Context-manager form: ``async with session.ws_connect(url) as ws:``.
    async def __aenter__(self) -> FakeWS:
        self._ws = _materialize(self._outcome, self._session)
        return self._ws

    async def __aexit__(self, *exc: object) -> bool:
        if self._ws is not None:
            await self._ws.close()
        return False


class FakeSession:
    """A scriptable stand-in for :class:`aiohttp.ClientSession`.

    Scripting knobs:

    - ``cmd_responses``: ``/cmd`` command name -> payload | ``FakeResponse`` |
      ``callable(params) -> ...``. An unmapped command falls back to
      ``default_response`` (a 200 with ``json`` -> ``None``), which makes setter
      ``/cmd`` writes succeed by default.
    - ``ws_outcomes``: a queue of per-connect outcomes (see :func:`_materialize`);
      once drained, ``default_ws_outcome`` is used for every further connect.

    Recording: every ``ws_connect`` and ``get`` call is captured for assertions.
    """

    def __init__(self) -> None:
        self.cmd_responses: dict[str, Any] = {}
        self.default_response: Any = None
        self.ws_outcomes: deque[Any] = deque()
        self.default_ws_outcome: Any = None
        self.ws_connect_calls: list[SimpleNamespace] = []
        self.ws_instances: list[FakeWS] = []
        self.get_calls: list[SimpleNamespace] = []
        self.closed = False
        self.close_calls = 0

    # -- WebSocket -------------------------------------------------------- #
    def ws_connect(self, url: str, **kwargs: Any) -> _WSConnectContext:
        self.ws_connect_calls.append(SimpleNamespace(url=url, kwargs=kwargs))
        outcome = (
            self.ws_outcomes.popleft() if self.ws_outcomes else self.default_ws_outcome
        )
        return _WSConnectContext(self, outcome)

    # -- HTTP getter/setter ---------------------------------------------- #
    def get(
        self, url: str, *, params: dict[str, str] | None = None, **kwargs: Any
    ) -> _GetContext:
        recorded = dict(params or {})
        self.get_calls.append(SimpleNamespace(url=url, params=recorded, kwargs=kwargs))
        return _GetContext(self, recorded)

    def _resolve_response(self, params: dict[str, str]) -> FakeResponse:
        cmd = params.get("cmd")
        entry = self.cmd_responses.get(cmd, self.default_response)
        if callable(entry) and not isinstance(entry, FakeResponse):
            entry = entry(params)
        if isinstance(entry, FakeResponse):
            return entry
        return FakeResponse(json_data=entry)

    async def close(self) -> None:
        self.closed = True
        self.close_calls += 1

    # -- Convenience getters for assertions ------------------------------ #
    def params_for(self, cmd: str) -> dict[str, str]:
        """Return the recorded query params of the last ``get`` for ``cmd``."""
        for call in reversed(self.get_calls):
            if call.params.get("cmd") == cmd:
                return call.params
        raise AssertionError(f"no GET recorded for cmd={cmd!r}")


# --------------------------------------------------------------------------- #
# Fixtures                                                                     #
# --------------------------------------------------------------------------- #
@pytest.fixture
def fake_clock() -> FakeClock:
    """A ``FakeClock`` pinned to 2026-05-25 10:00:00 UTC (the fixture date)."""
    return FakeClock()


@pytest.fixture
def fake_session() -> FakeSession:
    """A fresh, scriptable ``FakeSession``."""
    return FakeSession()


@pytest.fixture
def make_response() -> type[FakeResponse]:
    """Expose the ``FakeResponse`` class for scripting malformed / non-200 cases."""
    return FakeResponse


@pytest.fixture
def make_ws() -> Callable[..., FakeWS]:
    """Return a factory building a ``FakeWS`` from a list of ``WSMessage``s."""

    def _make(
        messages: Iterable[aiohttp.WSMessage] = (), *, blocking: bool = False
    ) -> FakeWS:
        return FakeWS(messages, blocking=blocking)

    return _make


@pytest.fixture
def load_fixture() -> Callable[[str], Any]:
    """Return a loader for ``tests/fixtures/<name>.json``."""

    def _load(name: str) -> Any:
        path = _FIXTURES / (name if name.endswith(".json") else f"{name}.json")
        return json.loads(path.read_text())

    return _load


@pytest.fixture
async def make_client(fake_session: FakeSession, fake_clock: FakeClock):
    """Factory building an ``Rtl433Client`` wired to the fake session + clock.

    Every client is stopped on teardown so no connect/refresh task leaks past the
    test (which would surface as a warning under ``filterwarnings=["error"]``).
    """
    created: list[Rtl433Client] = []

    def _make(**kwargs: Any) -> Rtl433Client:
        kwargs.setdefault("session", fake_session)
        kwargs.setdefault("clock", fake_clock)
        host = kwargs.pop("host", "rtl433.local")
        client = Rtl433Client(host, **kwargs)
        created.append(client)
        return client

    yield _make

    for client in created:
        await client.stop()
