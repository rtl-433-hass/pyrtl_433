"""Mutation-floor tests for pyrtl_433.replay.

Exact-value and both-branch assertions that pin every comparison boundary and
each verdict field of :func:`classify_replay`, plus the format/branch logic of
:func:`parse_event_time`, so small mutations (wrong operator, wrong bound,
dropped format, min->max) cause at least one assertion to fail.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from pyrtl_433.replay import (
    DISCOVERY_BACKLOG_GRACE,
    REPLAY_STALE_THRESHOLD,
    TimePrecision,
    classify_replay,
    parse_event_time,
    time_precision,
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
        """Every parsed instant is tz-aware and in UTC (kills the drop-as_utc mutant).

        ``tzinfo is UTC`` (not merely a zero ``utcoffset``) is what pins the
        documented "single comparable UTC basis": ``astimezone(None)`` returns
        the *system local* zone, which is the same instant -- so every equality
        assertion still passes -- but is only zero-offset on a UTC host. On a CI
        runner that happens to be UTC the weaker check silently admits it.
        """
        for raw in ("2026-05-25 10:00:00", "2026-05-25T10:00:00+05:30"):
            parsed = parse_event_time(raw)
            assert parsed is not None
            assert parsed.tzinfo is UTC
            assert parsed.utcoffset() == timedelta(0)

    def test_surrounding_whitespace_is_stripped(self):
        """Leading/trailing whitespace is stripped before parsing."""
        assert parse_event_time("  2026-05-25T10:00:00Z  ") == _utc(10, 0, 0)

    def test_default_tz_interprets_naive_wall_clock(self):
        """A naive local time is interpreted in ``default_tz``, not the host zone.

        2026-05-25 is EDT (UTC-4) in New York, so 10:00 local -> 14:00 UTC. This
        is independent of the process ``TZ`` (the whole point of the parameter).
        """
        ny = ZoneInfo("America/New_York")
        assert parse_event_time("2026-05-25 10:00:00", default_tz=ny) == _utc(14, 0, 0)

    def test_default_tz_ignored_for_offset_aware(self):
        """An offset-aware value ignores ``default_tz`` and converts as-is."""
        ny = ZoneInfo("America/New_York")
        assert parse_event_time("2026-05-25T10:00:00+05:30", default_tz=ny) == _utc(
            4, 30, 0
        )

    def test_default_tz_none_matches_system_local(self):
        """Omitting ``default_tz`` preserves the system-local interpretation."""
        raw = "2026-05-25 10:00:00"
        assert parse_event_time(raw) == datetime(2026, 5, 25, 10, 0, 0).astimezone(UTC)

    @pytest.mark.parametrize(
        "raw",
        [None, "", "   ", "not a timestamp", "garbage", 12345, 1.5, [], {}],
    )
    def test_unparsable_or_non_string_is_none(self, raw):
        """Missing / blank / garbage / non-string -> None, never raising."""
        assert parse_event_time(raw) is None

    def test_strptime_fallback_no_fraction(self):
        """A non-zero-padded local time (rejected by ``fromisoformat``) uses the
        no-fraction ``strptime`` fallback format.

        ``"2026-5-9 3:4:5"`` is not valid ISO-8601 (unpadded), so it drops into the
        explicit-format fallback loop and matches ``"%Y-%m-%d %H:%M:%S"``. This is
        the only path that exercises that second format string / the loop's
        ``strptime``+``break``+``continue`` structure, so it kills the mutants that
        mangle the second format, replace ``strptime`` with ``None``, turn the
        ``break`` into a ``return``, or turn the ``except`` ``continue`` into a
        ``break`` (which would abandon the loop before reaching this format).
        """
        parsed = parse_event_time("2026-5-9 3:4:5")
        assert parsed is not None
        expected = datetime(2026, 5, 9, 3, 4, 5).astimezone(UTC)
        assert parsed == expected

    def test_strptime_fallback_with_fraction(self):
        """A non-zero-padded local time *with* fractional seconds uses the first
        ``strptime`` fallback format (``%...%S.%f``).

        ``"2026-5-9 3:4:5.5"`` is rejected by ``fromisoformat`` (unpadded) and only
        the ``"%Y-%m-%d %H:%M:%S.%f"`` fallback format parses it (the no-fraction
        format fails on the trailing ``.5``). Pins the fractional fallback format
        and preserves the ``.500000`` component.
        """
        parsed = parse_event_time("2026-5-9 3:4:5.5")
        assert parsed is not None
        expected = datetime(2026, 5, 9, 3, 4, 5, 500000).astimezone(UTC)
        assert parsed == expected


# --------------------------------------------------------------------------- #
# _parse_epoch: exact instants and both plausibility bounds.                   #
# --------------------------------------------------------------------------- #
class TestParseEpoch:
    """Pin the epoch form's exact instants, both window bounds, and the guard."""

    def test_integer_epoch_exact_utc(self):
        """Integer epoch seconds reduce to the exact UTC instant."""
        assert parse_event_time("1779706800") == _utc(11, 0, 0)

    def test_fractional_epoch_preserves_microseconds(self):
        """``time:unix:usec`` keeps its sub-second component.

        Kills a mutant that truncates the value to whole seconds (``int`` for
        ``float``): .125 is exactly representable, so the comparison is stable.
        """
        assert parse_event_time("1779706800.125") == _utc(11, 0, 0, 125000)

    def test_lower_bound_is_inclusive(self):
        """The window's first instant is accepted (pins ``<=`` against ``<``)."""
        assert parse_event_time("946684800") == datetime(2000, 1, 1, tzinfo=UTC)

    def test_one_second_below_lower_bound_is_rejected(self):
        """One second under the window is rejected (pins the bound's value)."""
        assert parse_event_time("946684799") is None

    def test_upper_bound_is_exclusive(self):
        """The window's end instant is rejected (pins ``<`` against ``<=``)."""
        assert parse_event_time("4102444800") is None

    def test_one_second_below_upper_bound_is_accepted(self):
        """One second under the upper bound is still accepted."""
        assert parse_event_time("4102444799") == datetime(
            2099, 12, 31, 23, 59, 59, tzinfo=UTC
        )

    def test_default_tz_does_not_shift_an_epoch(self):
        """An epoch is absolute; ``default_tz`` applies only to naive wall-clocks.

        Kills a mutant that routes the epoch result through the naive-attach
        branch, which would shift it by the zone's offset.
        """
        ny = ZoneInfo("America/New_York")
        assert parse_event_time("1779706800", default_tz=ny) == _utc(11, 0, 0)

    @pytest.mark.parametrize("raw", ["nan", "inf", "-inf", "1e400", "-1e400"])
    def test_float_specials_are_rejected(self, raw):
        """``float()`` accepts these, so only the guard stops them.

        Pins the ``OSError, OverflowError, ValueError`` catch around
        ``fromtimestamp``: without it these propagate out of the frame loop.
        """
        assert parse_event_time(raw) is None

    def test_non_numeric_text_is_rejected(self):
        """Text that is neither a datetime form nor a number -> None.

        Pins the ``ValueError`` catch around ``float()``.
        """
        assert parse_event_time("not-a-number") is None


# --------------------------------------------------------------------------- #
# time_precision: every branch and the raw-text (not parsed-value) signal.     #
# --------------------------------------------------------------------------- #
class TestTimePrecision:
    """Pin all three outcomes, the enum values, and the regex's exact shape."""

    def test_enum_values_are_the_documented_strings(self):
        """Consumers key repairs off these; pins them against a renamed member."""
        assert TimePrecision.MICROSECOND == "microsecond"
        assert TimePrecision.SECOND == "second"
        assert TimePrecision.UNUSABLE == "unusable"

    def test_non_string_short_circuits_before_parsing(self):
        """A non-string is UNUSABLE without reaching the regex.

        Pins the ``isinstance`` guard: without it ``_SUBSECOND.search`` raises
        ``TypeError`` on a non-string rather than returning a verdict.
        """
        assert time_precision(12345) == TimePrecision.UNUSABLE
        assert time_precision(None) == TimePrecision.UNUSABLE

    def test_parseable_but_no_fraction_is_second(self):
        """Pins the SECOND fall-through against a MICROSECOND default."""
        assert time_precision("2026-05-25T10:00:00Z") == TimePrecision.SECOND

    def test_fraction_is_microsecond(self):
        """Pins the MICROSECOND branch against a SECOND default."""
        assert time_precision("2026-05-25T10:00:00.5Z") == TimePrecision.MICROSECOND

    def test_zero_fraction_is_still_microsecond(self):
        """``.000000`` is a format signal, not a value signal.

        Kills a mutant that reads ``parsed.microsecond`` instead of the raw text.
        """
        assert time_precision("2026-05-25T10:00:00.000000Z") == (
            TimePrecision.MICROSECOND
        )

    def test_trailing_dot_without_digits_is_not_a_fraction(self):
        """Pins the digit requirement in the sub-second pattern.

        ``float`` accepts a trailing dot, so ``"1779706800."`` parses -- but it
        carries no sub-second component and must not be read as one. Kills a
        mutant that drops the trailing digit class and matches a bare dot.
        """
        assert time_precision("1779706800.") == TimePrecision.SECOND


# --------------------------------------------------------------------------- #
# No EQUIVALENT mutants remain on parse_event_time.                            #
# --------------------------------------------------------------------------- #
# This file previously recorded ``if parsed.tzinfo is None:`` -> ``is not None``
# as genuinely equivalent. It is not: with ``default_tz`` supplied, the mutant
# skips the attach and lets ``astimezone`` assume the *system local* zone, so
# ``test_default_tz_interprets_naive_wall_clock`` distinguishes the two on any
# host whose local zone is not the injected one. It is killed.
#
# The only other candidate, ``astimezone(UTC)`` -> ``astimezone(None)``, is
# likewise not equivalent -- it returns the same instant in the system local
# zone rather than UTC -- and is killed by ``test_result_is_utc_aware`` above.
