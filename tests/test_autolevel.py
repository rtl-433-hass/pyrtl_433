"""Exact-value tests for :mod:`pyrtl_433.autolevel`.

The parser anchors on the two upstream message forms verbatim (``src/r_flow.c``,
stable wording since rtl_433 release 21.12). These tests pin both happy paths to
exact floats and pin the fail-safe: anything else — truncations, prefixes, other
"Auto Level"-adjacent wordings — must parse to ``None`` so a consumer can never
store a misread value.
"""

from __future__ import annotations

import pytest

from pyrtl_433.autolevel import AutoLevelReading, parse_auto_level


def test_parse_estimated_adjustment_message() -> None:
    """The -Y autolevel adjustment form yields noise + minimum level."""
    reading = parse_auto_level(
        "Estimated noise level is -38.4 dB, adjusting minimum detection level to -35.4 dB"
    )

    assert reading == AutoLevelReading(noise_db=-38.4, min_level_db=-35.4)


def test_parse_current_noise_message() -> None:
    """The -M noise periodic form yields the estimated noise only."""
    reading = parse_auto_level("Current noise level -38.2 dB, estimated noise -38.4 dB")

    assert reading == AutoLevelReading(noise_db=-38.4, min_level_db=None)


def test_parse_current_signal_variant() -> None:
    """The in-transmission 'signal' variant still yields the estimated noise."""
    reading = parse_auto_level(
        "Current signal level -20.1 dB, estimated noise -39.0 dB"
    )

    assert reading == AutoLevelReading(noise_db=-39.0, min_level_db=None)


def test_parse_positive_and_integer_levels() -> None:
    """Unsigned/integer dB renderings (%.1f never emits them, but stay tolerant)."""
    reading = parse_auto_level(
        "Estimated noise level is 3 dB, adjusting minimum detection level to 6.0 dB"
    )

    assert reading == AutoLevelReading(noise_db=3.0, min_level_db=6.0)


@pytest.mark.parametrize(
    "msg",
    [
        "",
        "Estimated noise level is -38.4 dB",  # truncated
        "Estimated noise level is high, adjusting minimum detection level to -35 dB",
        "XEstimated noise level is -38.4 dB, adjusting minimum detection level to -35.4 dB",
        "Estimated noise level is -38.4 dB, adjusting minimum detection level to -35.4 dBm",
        "Current squelch level -38.2 dB, estimated noise -38.4 dB",  # unknown kind
        "Current noise level -38.2 dB",  # truncated
        "Detector noise level is -38.4 dB",  # different wording
    ],
)
def test_unrecognized_messages_parse_to_none(msg: str) -> None:
    """Anything but the two exact upstream forms parses to None (fail-safe)."""
    assert parse_auto_level(msg) is None
