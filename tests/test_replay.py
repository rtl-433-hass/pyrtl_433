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
    TimePrecision,
    classify_replay,
    parse_event_time,
    payload_identity,
    time_precision,
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
# payload_identity + the same-stamp payload exception.                         #
# --------------------------------------------------------------------------- #
def test_payload_identity_ignores_per_decode_signal_levels():
    """Receiver-measured levels are excluded; device data is not.

    rssi/snr/noise/freq are measured separately for each decode of one
    transmission, so including them would make every repeat look distinct.
    """
    burst_1 = {"state": "open", "rssi": -11.2, "snr": 9.4, "noise": -20.6}
    burst_2 = {"state": "open", "rssi": -12.8, "snr": 8.1, "noise": -21.9}
    assert payload_identity(burst_1) == payload_identity(burst_2)
    # A real change in device data still separates them.
    assert payload_identity({"state": "closed"}) != payload_identity(burst_1)


def test_payload_identity_is_order_independent_and_handles_odd_values():
    """Key order never matters, and unhashable values do not raise."""
    assert payload_identity({"a": 1, "b": 2}) == payload_identity({"b": 2, "a": 1})
    # Some rtl_433 payloads carry lists/dicts; ``repr`` gives them a stable form.
    assert payload_identity({"rows": [1, 2]}) == payload_identity({"rows": [1, 2]})
    assert payload_identity({}) == ()


def test_same_stamp_with_a_new_payload_is_live():
    """One device's second transmission inside a stamp's resolution is live.

    At rtl_433's default 1-second resolution both transmissions carry the same
    ``time``, so the mark alone cannot separate them -- but the payload can, and
    suppressing the second would drop a real state change.
    """
    verdict = classify_replay(
        _utc(10, 0, 0),
        _utc(10, 0, 0),
        high_water=_utc(10, 0, 0),
        connection_time=_utc(9, 0, 0),
        payload=payload_identity({"state": "closed"}),
        seen_payloads={payload_identity({"state": "open"})},
    )
    assert verdict.is_replay is False
    assert verdict.label == "LIVE (payload differs at same high_water stamp)"


def test_same_stamp_with_the_same_payload_is_a_replay():
    """A repeat of one transmission is still suppressed."""
    same = payload_identity({"state": "open"})
    verdict = classify_replay(
        _utc(10, 0, 0),
        _utc(10, 0, 0),
        high_water=_utc(10, 0, 0),
        connection_time=_utc(9, 0, 0),
        payload=same,
        seen_payloads={same},
    )
    assert verdict.is_replay is True
    assert verdict.new_high_water is None  # the mark is left alone


def test_a_strictly_older_stamp_stays_a_replay_whatever_it_carries():
    """The payload exception is for equal stamps only.

    A frame timestamped *before* the mark is genuinely in the past -- a re-sent
    buffer tail, or an out-of-order delivery -- and differing data does not make
    it new.
    """
    verdict = classify_replay(
        _utc(10, 0, 0),
        _utc(10, 0, 5),
        high_water=_utc(10, 0, 1),
        connection_time=_utc(9, 0, 0),
        payload=payload_identity({"temperature_C": 20.5}),
        seen_payloads={payload_identity({"temperature_C": 21.0})},
    )
    assert verdict.is_replay is True
    assert verdict.label == "REPLAY (event_time<=high_water)"


def test_a_payload_seen_earlier_at_the_same_stamp_is_still_a_replay():
    """Membership, not adjacency: repeats need not be consecutive.

    A receiver decoding several devices interleaves them, so the frame before a
    repeat is often some other transmission -- and on a reconnect inside the
    backlog gate's grace window the server re-sends the whole tail. Either way
    the payload was already seen at this instant.
    """
    verdict = classify_replay(
        _utc(10, 0, 0),
        _utc(10, 0, 0),
        high_water=_utc(10, 0, 0),
        connection_time=_utc(9, 0, 0),
        payload=payload_identity({"state": "open"}),
        seen_payloads={
            payload_identity({"state": "open"}),
            payload_identity({"state": "closed"}),
        },
    )
    assert verdict.is_replay is True


def test_omitting_payloads_keeps_the_timestamp_only_behaviour():
    """A caller that supplies no payload information gets the old classifier."""
    verdict = classify_replay(
        _utc(10, 0, 0),
        _utc(10, 0, 0),
        high_water=_utc(10, 0, 0),
        connection_time=None,
    )
    assert verdict.is_replay is True


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
# time_precision: what the server's stamp format allows a client to tell apart. #
# --------------------------------------------------------------------------- #
def test_time_precision_reads_the_stamp_format():
    """Each accepted family is classified by whether it carries a sub-second part."""
    # rtl_433's default: parseable, whole seconds only.
    assert time_precision("2026-05-25 10:00:00") == TimePrecision.SECOND
    assert time_precision("2026-05-25T10:00:00Z") == TimePrecision.SECOND
    assert time_precision("1779706800") == TimePrecision.SECOND
    # ``report_meta time:...usec...`` in each base format.
    assert time_precision("2026-05-25 10:00:00.5") == TimePrecision.MICROSECOND
    assert (
        time_precision("2026-05-25T10:00:00.123456-04:00") == TimePrecision.MICROSECOND
    )
    assert time_precision("1779706800.5") == TimePrecision.MICROSECOND


def test_time_precision_unusable_when_nothing_parses():
    """``time:off`` / a missing key / an unreadable form -> UNUSABLE.

    This is the state in which ``classify_replay`` short-circuits every frame to
    ``LIVE (no-timestamp)``, so replay suppression is off entirely.
    """
    assert time_precision(None) == TimePrecision.UNUSABLE
    assert time_precision("") == TimePrecision.UNUSABLE
    assert time_precision("not a timestamp") == TimePrecision.UNUSABLE
    assert time_precision(12345) == TimePrecision.UNUSABLE  # non-string


def test_time_precision_reads_the_format_not_the_value():
    """A ``.000000`` stamp is still a microsecond-precision server.

    A ``time:usec`` server stamps a whole-second instant roughly one frame in a
    million; reading ``parsed.microsecond`` instead of the raw text would
    misreport exactly those frames and make the signal flap.
    """
    assert time_precision("2026-05-25T10:00:00.000000Z") == TimePrecision.MICROSECOND


# --------------------------------------------------------------------------- #
# Thresholds are the documented durations.                                     #
# --------------------------------------------------------------------------- #
def test_threshold_constants():
    """The two skew constants keep their documented values."""
    assert timedelta(seconds=30) == REPLAY_STALE_THRESHOLD
    assert timedelta(seconds=5) == DISCOVERY_BACKLOG_GRACE
