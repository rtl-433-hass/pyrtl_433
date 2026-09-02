"""Reconnect-replay classification for the rtl_433 client library.

Extracted from custom_components/rtl_433/coordinator/_events.py of
rtl-433-hass/rtl_433 (Apache-2.0); the ``_parse_event_time`` staticmethod is
re-homed here as the module-level :func:`parse_event_time`, re-expressed with the
standard-library :mod:`datetime` instead of the Home Assistant ``dt_util`` helper.

On every (re)connect an rtl_433 server replays up to its last ~100 events. This
module classifies one event frame against that replay -- live vs already-seen
replay vs stale gap vs pre-connection backlog -- so a replayed frame can seed
sensor values without re-firing ``event`` entities or refreshing liveness.

The classification lives in the pure :func:`classify_replay` helper so it can be
reasoned about and unit-tested in isolation.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta, tzinfo
import logging
from typing import Any

LOGGER = logging.getLogger(__name__)

# Age boundary that separates a genuinely-fresh "live" event from a stale "gap"
# event in the reconnect replay. On every (re)connect the server replays up to
# its last 100 events; a frame whose ``time`` is newer than the high-water mark
# but older than this threshold occurred while the client was disconnected
# (a gap event) and must NOT re-fire automations or refresh liveness. Sized
# generously enough to absorb modest rtl_433-vs-client clock skew + transmission
# latency (so a real live event is never misjudged stale -- "never drop a real
# one") while staying shorter than a typical restart outage (so restart gap
# events are suppressed). Assumes the server and client clocks are roughly
# NTP-synced.
REPLAY_STALE_THRESHOLD = timedelta(seconds=30)

# Skew grace for the post-connection device-registration gate. A previously
# unknown device auto-registers only once a frame timestamped at or after
# ``connection_time - DISCOVERY_BACKLOG_GRACE`` is seen; older frames are the
# server's pre-connection backlog and seed runtime state without registering.
# The grace absorbs modest rtl_433-vs-client clock skew + transmission latency
# and assumes the server and client clocks are roughly NTP-synced (a frame with
# no parseable ``time`` is treated as live, preserving "never drop a real one").
DISCOVERY_BACKLOG_GRACE = timedelta(seconds=5)


@dataclasses.dataclass(frozen=True, slots=True)
class ReplayVerdict:
    """How one event frame was classified against the reconnect replay.

    ``is_replay`` is the headline outcome: a replay / stale gap / backlog frame
    seeds sensor values but must NOT re-fire ``event`` entities or refresh
    liveness. ``is_backlog`` is the independent "timestamped before this
    connection" signal that gates device auto-registration (a backlog frame can
    also be an already-seen replay). ``label`` is a short DEBUG-only description.
    ``new_high_water``, when not ``None``, is the value the caller should advance
    the event high-water mark to (``None`` leaves the mark unchanged).
    """

    is_replay: bool
    is_backlog: bool
    label: str
    new_high_water: datetime | None


def classify_replay(
    event_time: datetime | None,
    now: datetime,
    *,
    high_water: datetime | None,
    connection_time: datetime | None,
) -> ReplayVerdict:
    """Classify an event frame as live / replay / stale gap / backlog.

    Pure function of the frame's timestamp and three pieces of coordinator state,
    so the (otherwise dense) decision can be unit-tested directly. Two signals
    catch a non-live frame: the high-water mark catches an already-seen frame,
    and the event age catches an unseen-but-old gap event. A third signal, the
    connection-time gate, marks a recent-but-pre-connection frame as replayed
    backlog (the restart re-delivery case).

    ``event_time is None`` (no usable timestamp) and ``connection_time is None``
    (disconnected, or a direct unit-test feed) both keep ``is_backlog`` False and
    a timestamped frame live, preserving "never drop a real one".
    """
    # A frame timestamped before this connection began is part of the server's
    # reconnect-replay backlog (it occurred while the client was disconnected), so
    # it must never count as a live transmission -- even when recent enough to
    # pass the staleness test. This gate is independent of the replay outcome
    # below because an already-seen replay frame can also be pre-connection
    # backlog.
    is_backlog = (
        connection_time is not None
        and event_time is not None
        and event_time < connection_time - DISCOVERY_BACKLOG_GRACE
    )
    if event_time is None:
        # No usable timestamp -> treat as live ("never drop a real one").
        return ReplayVerdict(False, False, "LIVE (no-timestamp)", None)
    if high_water is not None and event_time <= high_water:
        # At or below the high-water mark -> an already-seen replay (catches the
        # re-sent buffer tail on a brief blip; never re-fires). Leave the mark.
        return ReplayVerdict(True, is_backlog, "REPLAY (event_time<=high_water)", None)
    if now - event_time > REPLAY_STALE_THRESHOLD:
        # Newer than the mark (never saw it) but old -> a stale gap event that
        # occurred while disconnected. Advance the mark so it is not reconsidered.
        return ReplayVerdict(True, is_backlog, "STALE-GAP (age>threshold)", event_time)
    if is_backlog:
        # Newer than the mark and recent, but timestamped before this connection
        # -> a replayed backlog frame, not a live transmission. Suppress it (so a
        # restart does not re-fire events) while advancing the mark.
        return ReplayVerdict(True, True, "BACKLOG (pre-connection)", event_time)
    # Newer than the mark and recent -> a genuine live transmission. Clamp the
    # high-water advance to ``now``: a frame stamped in the future (server clock
    # ahead of the client, or a one-off glitched timestamp) must not push the mark
    # past wall-clock time, or every subsequent correctly-stamped live frame would
    # fall at-or-below it and be wrongly suppressed as a replay -- stalling
    # availability and silencing event entities until wall-clock caught up. The
    # frame still fires as live; only the mark is bounded.
    return ReplayVerdict(
        False, False, "LIVE (event_time>high_water)", min(event_time, now)
    )


# Plausibility window for a bare epoch-seconds ``time`` value
# (``report_meta time:unix``). Unlike every other accepted form, a run of digits
# carries no structural marker that says "this is a timestamp", so an
# out-of-range value is far more likely a glitch than a real instant. Rejecting
# it is also the safer failure: a near-zero value would reduce to 1970, which is
# older than :data:`REPLAY_STALE_THRESHOLD` and would classify every frame
# STALE-GAP -- silently suppressing live traffic -- whereas ``None`` leaves the
# frame live, preserving "never drop a real one".
_EPOCH_MIN = datetime(2000, 1, 1, tzinfo=UTC)
_EPOCH_MAX = datetime(2100, 1, 1, tzinfo=UTC)


def _parse_epoch(text: str) -> datetime | None:
    """Parse an epoch-seconds ``time`` string to UTC, or ``None`` if implausible.

    Covers ``report_meta time:unix`` (integer seconds, e.g. ``"1779706800"``) and
    ``time:unix:usec`` (fractional, e.g. ``"1779706800.123456"``). rtl_433 emits
    ``time`` as a JSON string in every mode, so only the string form is accepted.
    An epoch is an absolute instant, so ``default_tz`` never applies to it.

    Values outside :data:`_EPOCH_MIN`..:data:`_EPOCH_MAX`, non-numeric text, and
    the ``float`` specials (``nan`` / ``inf``, which :func:`float` accepts) all
    yield ``None``.
    """
    try:
        value = float(text)
    except ValueError:
        return None
    try:
        parsed = datetime.fromtimestamp(value, UTC)
    except OSError, OverflowError, ValueError:
        # Outside the platform's representable range, or a nan/inf special.
        return None
    if not _EPOCH_MIN <= parsed < _EPOCH_MAX:
        return None
    return parsed


def _parse_local_naive(text: str) -> datetime | None:
    """Parse the explicit rtl_433 local ``time`` formats, or ``None`` if none match."""
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)  # noqa: DTZ007 - local naive
        except ValueError:
            continue
    return None


def parse_event_time(raw: Any, *, default_tz: tzinfo | None = None) -> datetime | None:
    """Parse an rtl_433 ``time`` value to a comparable UTC instant, or ``None``.

    rtl_433 stamps ``time`` as a local ``"YYYY-MM-DD HH:MM:SS"`` string
    (optionally with fractional seconds), as ISO-8601 with an offset / ``Z``, or
    as bare epoch seconds (``report_meta time:unix``, optionally fractional),
    depending on server config. This reduces all three to a single UTC basis so
    the replay classification can compare them. An unhandled form would leave
    every frame with no usable timestamp, which disables replay suppression
    entirely -- the whole backlog re-fires on reconnect -- so the epoch form is
    parsed rather than rejected. A local-naive value is interpreted in
    ``default_tz`` when one is supplied (e.g. the consumer's configured time zone,
    such as Home Assistant's), otherwise in the system local time zone (the
    NTP-sync assumption documented on :data:`REPLAY_STALE_THRESHOLD`); an
    offset-aware value is converted as-is and ``default_tz`` is ignored.

    A missing, blank, or unparsable value yields ``None`` ("no usable timestamp"
    -- the frame is then treated as live). Never raises into the frame loop.
    """
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    parsed: datetime | None
    try:
        # ``fromisoformat`` handles ISO-8601 with an offset / ``Z`` and, since
        # Python 3.11, the space-separated local ``"YYYY-MM-DD HH:MM:SS"`` form
        # (with optional fractional seconds) as a naive datetime.
        parsed = datetime.fromisoformat(text)
    except TypeError, ValueError:
        # A form ``fromisoformat`` rejects: fall back to the explicit rtl_433
        # local formats, then to bare epoch seconds, before giving up.
        parsed = _parse_local_naive(text)
        if parsed is None:
            return _parse_epoch(text)
    try:
        if parsed.tzinfo is None:
            if default_tz is not None:
                # Interpret the naive wall-clock in the caller-supplied zone
                # (mirrors ``dt_util.as_utc`` treating a naive datetime as the
                # consumer's DEFAULT_TIME_ZONE). ``replace`` is DST-correct for
                # that instant with a ``ZoneInfo``.
                parsed = parsed.replace(tzinfo=default_tz)
            else:
                # No zone supplied: fall back to the system local zone for that
                # specific instant, DST-correct.
                parsed = parsed.astimezone()
        # Reduce every form to a single comparable UTC basis.
        return parsed.astimezone(UTC)
    except OSError, OverflowError, ValueError:
        return None
