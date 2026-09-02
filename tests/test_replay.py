"""Behavioral tests for reconnect-replay classification and time parsing.

Extracted from the coordinator's replay scenarios in
tests/test_coordinator.py of rtl-433-hass/rtl_433 (Apache-2.0) and rewritten to
call :func:`pyrtl_433.replay.classify_replay` / :func:`parse_event_time`
directly -- no Home Assistant, no MockConfigEntry, no dt_util.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pyrtl_433.replay import (
    DISCOVERY_BACKLOG_GRACE,
    REPLAY_STALE_THRESHOLD,
    ReplayVerdict,
    classify_replay,
    parse_event_time,
)


def _utc(h: int = 10, m: int = 0, s: int = 0) -> datetime:
    """A UTC instant on the fixture date 2026-05-25."""
    return datetime(2026, 5, 25, h, m, s, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# classify_replay: the four outcomes.                                          #
# --------------------------------------------------------------------------- #
def test_no_timestamp_is_live():
    """event_time None -> live, no backlog, mark unchanged ("never drop a real one")."""
    verdict = classify_replay(
        None, _utc(10, 0, 0), high_water=_utc(9, 0, 0), connection_time=_utc(8, 0, 0)
    )
    assert verdict == ReplayVerdict(False, False, "LIVE (no-timestamp)", None)


def test_at_or_below_high_water_is_replay():
    """A frame at/below the mark is an already-seen replay; the mark is left alone."""
    # Equal to the mark (the re-sent buffer tail on a brief blip).
    v_eq = classify_replay(
        _utc(10, 0, 0),
        _utc(10, 0, 2),
        high_water=_utc(10, 0, 0),
        connection_time=None,
    )
    assert v_eq.is_replay is True
    assert v_eq.new_high_water is None
    assert v_eq.label == "REPLAY (event_time<=high_water)"

    # Strictly below the mark is also a replay.
    v_below = classify_replay(
        _utc(9, 59, 59),
        _utc(10, 0, 2),
        high_water=_utc(10, 0, 0),
        connection_time=None,
    )
    assert v_below.is_replay is True
    assert v_below.new_high_water is None


def test_stale_gap_event_is_suppressed_and_advances_mark():
    """Newer than the mark but older than the threshold -> stale gap, mark advances."""
    event_time = _utc(10, 1, 0)
    now = _utc(10, 5, 0)  # 240s later, comfortably beyond the 30s threshold
    assert now - event_time > REPLAY_STALE_THRESHOLD
    verdict = classify_replay(
        event_time, now, high_water=_utc(10, 0, 0), connection_time=None
    )
    assert verdict.is_replay is True
    assert verdict.label == "STALE-GAP (age>threshold)"
    # Advances the mark to the gap event's time so it is not reconsidered.
    assert verdict.new_high_water == event_time


def test_recent_backlog_event_is_suppressed():
    """Recent but pre-connection -> replayed backlog: suppressed, mark advances."""
    event_time = _utc(10, 0, 0)
    connection_time = _utc(10, 0, 10)  # event is 10s before connect, > 5s grace
    now = _utc(10, 0, 11)  # age 11s < 30s threshold
    assert now - event_time < REPLAY_STALE_THRESHOLD
    verdict = classify_replay(
        event_time, now, high_water=None, connection_time=connection_time
    )
    assert verdict.is_replay is True
    assert verdict.is_backlog is True
    assert verdict.label == "BACKLOG (pre-connection)"
    assert verdict.new_high_water == event_time


def test_fresh_post_connection_event_is_live():
    """Newer than the mark, recent, and after the connection -> a live transmission."""
    event_time = _utc(10, 0, 15)
    verdict = classify_replay(
        event_time,
        _utc(10, 0, 15),
        high_water=_utc(10, 0, 0),
        connection_time=_utc(10, 0, 10),
    )
    assert verdict.is_replay is False
    assert verdict.is_backlog is False
    assert verdict.label == "LIVE (event_time>high_water)"
    assert verdict.new_high_water == event_time


def test_future_timestamp_clamps_high_water_to_now():
    """A future-stamped live frame advances the mark only to ``now`` (never ahead)."""
    now = _utc(10, 0, 0)
    verdict = classify_replay(
        _utc(11, 0, 0), now, high_water=None, connection_time=None
    )
    # Still fires as live...
    assert verdict.is_replay is False
    # ...but the mark is clamped to ``now``, not pushed out to the future stamp.
    assert verdict.new_high_water == now


# --------------------------------------------------------------------------- #
# parse_event_time: format variance.                                           #
# --------------------------------------------------------------------------- #
def test_parse_local_naive_reduces_to_utc():
    """A local-naive time is interpreted in the local zone and reduced to UTC."""
    parsed = parse_event_time("2026-05-25 10:00:00")
    assert parsed is not None
    assert parsed.tzinfo is not None  # reduced to an aware (UTC) instant
    # Compare against the same local->UTC reduction so the test is tz-robust.
    expected = datetime(2026, 5, 25, 10, 0, 0).astimezone(UTC)
    assert parsed == expected
    # Optional fractional seconds parse too.
    assert parse_event_time("2026-05-25 10:00:00.5") is not None


def test_parse_iso_z_and_offset_reduce_to_utc():
    """ISO-8601 with ``Z`` or a numeric offset both reduce to the same UTC basis."""
    assert parse_event_time("2026-05-25T10:00:00Z") == _utc(10, 0, 0)
    # +02:00 means the UTC instant is two hours earlier.
    assert parse_event_time("2026-05-25T12:00:00+02:00") == _utc(10, 0, 0)


def test_parse_epoch_seconds_reduces_to_utc():
    """``report_meta time:unix`` stamps ``time`` as bare epoch seconds.

    rtl_433 emits this form as a JSON string like every other mode. Left
    unparsed it yields ``None`` for *every* frame, routing all traffic through
    the "no usable timestamp" branch -- which disables replay suppression
    entirely, so the server's whole backlog re-fires on each reconnect.
    """
    assert parse_event_time("1779706800") == _utc(11, 0, 0)
    # ``time:unix:usec`` adds a fractional part.
    assert parse_event_time("1779706800.5") == _utc(11, 0, 0) + timedelta(
        microseconds=500000
    )


def test_parse_rejects_junk_and_non_strings():
    """Missing / blank / garbage / non-string values -> None, never raising."""
    assert parse_event_time(None) is None
    assert parse_event_time("") is None
    assert parse_event_time("   ") is None
    assert parse_event_time("not a timestamp") is None
    assert parse_event_time(12345) is None  # non-string
    # A number outside the plausibility window is not believed as an epoch:
    # reducing it to 1970 would classify every frame STALE-GAP and suppress
    # live traffic, so it is rejected as unparsable instead.
    assert parse_event_time("12345") is None


# --------------------------------------------------------------------------- #
# Thresholds are the documented durations.                                     #
# --------------------------------------------------------------------------- #
def test_threshold_constants():
    """The two skew constants keep their documented values."""
    assert timedelta(seconds=30) == REPLAY_STALE_THRESHOLD
    assert timedelta(seconds=5) == DISCOVERY_BACKLOG_GRACE
