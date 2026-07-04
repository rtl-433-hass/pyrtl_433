"""Mutation-floor tests for pyrtl_433.replay.

Exact-value and both-branch assertions that pin every comparison boundary and
each verdict field of :func:`classify_replay`, plus the format/branch logic of
:func:`parse_event_time`, so small mutations (wrong operator, wrong bound,
dropped format, min->max) cause at least one assertion to fail.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from pyrtl_433.replay import (
    DISCOVERY_BACKLOG_GRACE,
    REPLAY_STALE_THRESHOLD,
    classify_replay,
    parse_event_time,
)


def _utc(h: int = 10, m: int = 0, s: int = 0, us: int = 0) -> datetime:
    return datetime(2026, 5, 25, h, m, s, us, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# classify_replay: high-water boundary (``event_time <= high_water``).         #
# --------------------------------------------------------------------------- #
class TestHighWaterBoundary:
    """Pin the ``<=`` boundary and the leave-the-mark behaviour of the replay."""

    def test_equal_to_high_water_is_replay(self):
        """event_time == high_water -> replay (kills ``<`` mutant)."""
        v = classify_replay(
            _utc(10, 0, 0),
            _utc(10, 0, 5),
            high_water=_utc(10, 0, 0),
            connection_time=None,
        )
        assert v.is_replay is True
        assert v.new_high_water is None  # the mark is left unchanged

    def test_one_microsecond_above_high_water_is_not_replay(self):
        """event_time just above the mark -> not a high-water replay (falls to live)."""
        v = classify_replay(
            _utc(10, 0, 0, 1),
            _utc(10, 0, 5),
            high_water=_utc(10, 0, 0),
            connection_time=None,
        )
        assert v.is_replay is False
        assert v.label == "LIVE (event_time>high_water)"

    def test_replay_carries_independent_backlog_flag(self):
        """An at-mark replay that is ALSO pre-connection reports is_backlog=True.

        Kills a mutant that hardcodes ``is_backlog=False`` in the replay branch:
        the two signals are independent.
        """
        v = classify_replay(
            _utc(10, 0, 0),
            _utc(10, 0, 11),
            high_water=_utc(10, 0, 0),
            connection_time=_utc(10, 0, 10),  # grace boundary at 10:00:05
        )
        assert v.is_replay is True
        assert v.is_backlog is True
        assert v.new_high_water is None


# --------------------------------------------------------------------------- #
# classify_replay: staleness threshold boundary (``> REPLAY_STALE_THRESHOLD``).#
# --------------------------------------------------------------------------- #
class TestStaleThresholdBoundary:
    """Pin the strict ``>`` boundary at exactly REPLAY_STALE_THRESHOLD."""

    def test_age_exactly_at_threshold_is_live(self):
        """age == threshold -> live (strict ``>`` kills the ``>=`` mutant)."""
        event_time = _utc(10, 0, 0)
        now = event_time + REPLAY_STALE_THRESHOLD  # exactly 30s
        v = classify_replay(event_time, now, high_water=None, connection_time=None)
        assert v.is_replay is False
        # Live -> mark advances to min(event_time, now) = event_time.
        assert v.new_high_water == event_time

    def test_age_one_second_over_threshold_is_stale(self):
        """age just over threshold -> stale gap, mark advances to event_time."""
        event_time = _utc(10, 0, 0)
        now = event_time + REPLAY_STALE_THRESHOLD + timedelta(seconds=1)
        v = classify_replay(event_time, now, high_water=None, connection_time=None)
        assert v.is_replay is True
        assert v.label == "STALE-GAP (age>threshold)"
        assert v.new_high_water == event_time  # not None, not now


# --------------------------------------------------------------------------- #
# classify_replay: backlog gate boundary (``< connection_time - grace``).      #
# --------------------------------------------------------------------------- #
class TestBacklogGraceBoundary:
    """Pin the strict ``<`` boundary of the connection-time backlog gate."""

    def test_exactly_at_grace_boundary_is_live(self):
        """event_time == connection_time - grace -> NOT backlog (strict ``<``)."""
        connection_time = _utc(10, 0, 10)
        event_time = connection_time - DISCOVERY_BACKLOG_GRACE  # 10:00:05 exactly
        v = classify_replay(
            event_time,
            _utc(10, 0, 11),
            high_water=None,
            connection_time=connection_time,
        )
        assert v.is_backlog is False
        assert v.is_replay is False
        assert v.label == "LIVE (event_time>high_water)"

    def test_one_microsecond_inside_grace_boundary_is_backlog(self):
        """event_time just before the boundary -> backlog, suppressed."""
        connection_time = _utc(10, 0, 10)
        event_time = (
            connection_time - DISCOVERY_BACKLOG_GRACE - timedelta(microseconds=1)
        )
        v = classify_replay(
            event_time,
            _utc(10, 0, 11),
            high_water=None,
            connection_time=connection_time,
        )
        assert v.is_backlog is True
        assert v.is_replay is True
        assert v.label == "BACKLOG (pre-connection)"
        assert v.new_high_water == event_time

    def test_connection_time_none_never_backlog(self):
        """connection_time None -> is_backlog stays False even for an old frame."""
        v = classify_replay(
            _utc(9, 0, 0),
            _utc(10, 0, 0),
            high_water=None,
            connection_time=None,
        )
        assert v.is_backlog is False


# --------------------------------------------------------------------------- #
# classify_replay: the ``min(event_time, now)`` clamp on the live branch.      #
# --------------------------------------------------------------------------- #
class TestLiveHighWaterClamp:
    """The live branch advances the mark to min(event_time, now), never max."""

    def test_future_stamp_clamps_to_now(self):
        """A frame stamped ahead of now advances the mark only to now."""
        now = _utc(10, 0, 0)
        v = classify_replay(_utc(11, 0, 0), now, high_water=None, connection_time=None)
        assert v.is_replay is False
        assert v.new_high_water == now  # not the 11:00 future stamp

    def test_past_within_window_advances_to_event_time(self):
        """A recent-but-behind-now live frame advances the mark to event_time."""
        event_time = _utc(10, 0, 0)
        now = _utc(10, 0, 20)  # 20s < 30s threshold -> still live
        v = classify_replay(event_time, now, high_water=None, connection_time=None)
        assert v.is_replay is False
        assert v.new_high_water == event_time  # min picks the earlier event_time


# --------------------------------------------------------------------------- #
# classify_replay: the no-timestamp early return precedes every other branch.  #
# --------------------------------------------------------------------------- #
def test_no_timestamp_short_circuits_before_high_water():
    """event_time None returns live even when high_water/connection would suppress."""
    v = classify_replay(
        None,
        _utc(10, 0, 0),
        high_water=_utc(23, 0, 0),  # would REPLAY a timestamped frame
        connection_time=_utc(23, 0, 0),
    )
    assert v.is_replay is False
    assert v.is_backlog is False
    assert v.new_high_water is None
    assert v.label == "LIVE (no-timestamp)"


# --------------------------------------------------------------------------- #
# parse_event_time: format + branch logic, exact values.                       #
# --------------------------------------------------------------------------- #
class TestParseEventTime:
    """Exact-value assertions across every accepted / rejected time form."""

    def test_iso_z_exact_utc(self):
        """ISO-8601 + Z is unambiguous UTC."""
        assert parse_event_time("2026-05-25T10:00:00Z") == _utc(10, 0, 0)

    def test_iso_offset_exact_utc(self):
        """A +05:30 offset reduces to 04:30 UTC (kills sign/convert mutants)."""
        assert parse_event_time("2026-05-25T10:00:00+05:30") == _utc(4, 30, 0)

    def test_space_separated_preserves_microseconds(self):
        """The fractional-second local form keeps its microseconds.

        Kills a mutant that drops the ``%f`` format / truncates: the parsed
        instant must carry the .500000 second component.
        """
        parsed = parse_event_time("2026-05-25 10:00:00.500000")
        assert parsed is not None
        expected = datetime(2026, 5, 25, 10, 0, 0, 500000).astimezone(UTC)
        assert parsed == expected

    def test_space_separated_without_microseconds(self):
        """The no-fraction local form parses to the local->UTC reduction."""
        parsed = parse_event_time("2026-05-25 10:00:00")
        assert parsed is not None
        expected = datetime(2026, 5, 25, 10, 0, 0).astimezone(UTC)
        assert parsed == expected

    def test_result_is_utc_aware(self):
        """Every parsed instant is tz-aware and in UTC (kills the drop-as_utc mutant)."""
        for raw in ("2026-05-25 10:00:00", "2026-05-25T10:00:00+05:30"):
            parsed = parse_event_time(raw)
            assert parsed is not None
            assert parsed.tzinfo is not None
            assert parsed.utcoffset() == timedelta(0)

    def test_surrounding_whitespace_is_stripped(self):
        """Leading/trailing whitespace is stripped before parsing."""
        assert parse_event_time("  2026-05-25T10:00:00Z  ") == _utc(10, 0, 0)

    @pytest.mark.parametrize(
        "raw",
        [None, "", "   ", "not a timestamp", "garbage", 12345, 1.5, [], {}],
    )
    def test_unparsable_or_non_string_is_none(self, raw):
        """Missing / blank / garbage / non-string -> None, never raising."""
        assert parse_event_time(raw) is None
