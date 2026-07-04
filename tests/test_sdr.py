"""Behavioral tests for the SDR command registry (protocol transforms).

Extracted from the SDR-transform scenarios in tests/test_sdr_controls.py of
rtl-433-hass/rtl_433 (Apache-2.0) and rewritten to call
:mod:`pyrtl_433.sdr` directly -- no Home Assistant entities, no coordinator.
"""

from __future__ import annotations

from pyrtl_433.sdr import (
    CONVERSION_MODES,
    KEY_CENTER_FREQUENCY,
    KEY_CONVERSION_MODE,
    KEY_GAIN_AUTO,
    KEY_GAIN_DB,
    KEY_HOP_INTERVAL,
    KEY_PPM_ERROR,
    KEY_SAMPLE_RATE,
    SDR_COMMANDS,
    SDR_COMMANDS_BY_KEY,
    available_when_hopping,
    available_when_not_hopping,
    conversion_label_to_val,
    conversion_val_to_label,
    frequency_count,
    gain_command_arg,
    int_command,
    mhz_to_hz_command,
    read_center_frequency,
)

_META_SINGLE = {
    "center_frequency": 433920000,
    "samp_rate": 250000,
    "conversion_mode": 1,
    "frequencies": [433920000],
    "hop_times": [600],
    "hop_interval": 600,
    "gain": "32.8",
    "ppm_error": 2,
}
_META_HOPPING = {**_META_SINGLE, "frequencies": [433920000, 868000000]}


# --------------------------------------------------------------------------- #
# gain_command_arg                                                             #
# --------------------------------------------------------------------------- #
def test_gain_command_arg_auto_is_empty():
    """Auto gain -> empty arg (the auto sentinel), regardless of dB value."""
    assert gain_command_arg(32.8, gain_auto=True) == ""
    assert gain_command_arg(None, gain_auto=False) == ""


def test_gain_command_arg_manual_uses_g_format():
    """Manual gain formats with %g: clean integers drop the trailing .0."""
    assert gain_command_arg(40.0, gain_auto=False) == "40"
    assert gain_command_arg(32.8, gain_auto=False) == "32.8"
    assert gain_command_arg(0.0, gain_auto=False) == "0"


# --------------------------------------------------------------------------- #
# Conversion-mode mapping                                                      #
# --------------------------------------------------------------------------- #
def test_conversion_modes_order_is_load_bearing():
    """The tuple order defines the ``convert`` command ``val``: native/si/customary."""
    assert CONVERSION_MODES == ("native", "si", "customary")


def test_conversion_label_val_round_trip():
    """label->val and val->label invert each other for every known mode."""
    for i, label in enumerate(CONVERSION_MODES):
        assert conversion_label_to_val(label) == i
        assert conversion_val_to_label(i) == label


def test_conversion_val_to_label_unknown_is_none():
    """An out-of-range index yields None rather than raising."""
    assert conversion_val_to_label(len(CONVERSION_MODES)) is None
    assert conversion_val_to_label(-1) is None


# --------------------------------------------------------------------------- #
# Outbound value transforms                                                    #
# --------------------------------------------------------------------------- #
def test_int_command_coerces_to_int():
    """int_command coerces the desired value to the integer sent on ``val``."""
    assert int_command(2.0) == 2
    assert int_command("5") == 5
    assert isinstance(int_command(3.9), int)  # truncates toward zero


def test_center_frequency_round_trip():
    """read converts meta Hz -> MHz; to_command converts MHz -> integer Hz."""
    cf = SDR_COMMANDS_BY_KEY[KEY_CENTER_FREQUENCY]

    assert cf.read({"center_frequency": 915_000_000}) == 915.0
    assert cf.read({"center_frequency": 433_920_000}) == 433.92

    for mhz, hz in ((915.0, 915_000_000), (433.92, 433_920_000), (868.3, 868_300_000)):
        sent = cf.to_command(mhz)
        assert sent == hz
        assert isinstance(sent, int)
    # The module-level transforms agree with the registry wiring.
    assert cf.to_command is mhz_to_hz_command
    assert read_center_frequency({"center_frequency": 868_300_000}) == 868.3


def test_conversion_command_maps_label_via_int_command():
    """The select writes conversion_label_to_val, then to_command sends the int."""
    cmd = SDR_COMMANDS_BY_KEY[KEY_CONVERSION_MODE]
    val = conversion_label_to_val("si")
    assert val == 1
    assert cmd.arg_kind == "val"
    assert cmd.command == "convert"
    assert cmd.to_command(val) == 1


def test_gain_pair_shares_command_and_composes_arg():
    """The gain dB Number and the auto-gain Switch share the ``gain`` command."""
    gain_db = SDR_COMMANDS_BY_KEY[KEY_GAIN_DB]
    gain_auto = SDR_COMMANDS_BY_KEY[KEY_GAIN_AUTO]
    assert gain_db.command == "gain"
    assert gain_auto.command == "gain"
    assert gain_db.arg_kind == "arg"
    assert gain_auto.arg_kind == "arg"
    # dB Number: auto is off -> the dB value rides on arg.
    assert gain_db.to_command(32.8) == "32.8"
    # Auto Switch on -> empty arg (auto); off -> empty (defers to the dB value).
    assert gain_auto.to_command(True) == ""
    assert gain_auto.to_command(False) == ""


# --------------------------------------------------------------------------- #
# Capability / availability gates                                             #
# --------------------------------------------------------------------------- #
def test_frequency_count():
    """frequency_count counts the list, or None when the key is missing/not a list."""
    assert frequency_count(_META_SINGLE) == 1
    assert frequency_count(_META_HOPPING) == 2
    assert frequency_count({}) is None
    assert frequency_count({"frequencies": "nope"}) is None


def test_center_and_hop_availability_track_frequencies():
    """center_frequency hides under hop mode; hop_interval hides under single-freq."""
    cf = SDR_COMMANDS_BY_KEY[KEY_CENTER_FREQUENCY]
    hop = SDR_COMMANDS_BY_KEY[KEY_HOP_INTERVAL]

    # Single frequency: center available, hop hidden.
    assert cf.available(_META_SINGLE) is True
    assert hop.available(_META_SINGLE) is False

    # Hopping: center hidden, hop available.
    assert cf.available(_META_HOPPING) is False
    assert hop.available(_META_HOPPING) is True

    # Unknown count (pre-connect) defaults both to available.
    assert available_when_not_hopping({}) is True
    assert available_when_hopping({}) is True


# --------------------------------------------------------------------------- #
# Registry shape                                                               #
# --------------------------------------------------------------------------- #
def test_registry_has_all_seven_fields():
    """SDR_COMMANDS carries the seven managed fields, indexed by stable key."""
    expected = {
        KEY_CENTER_FREQUENCY,
        KEY_SAMPLE_RATE,
        KEY_PPM_ERROR,
        KEY_GAIN_DB,
        KEY_GAIN_AUTO,
        KEY_CONVERSION_MODE,
        KEY_HOP_INTERVAL,
    }
    assert {c.key for c in SDR_COMMANDS} == expected
    assert set(SDR_COMMANDS_BY_KEY) == expected
    for key, cmd in SDR_COMMANDS_BY_KEY.items():
        assert cmd.key == key
        # Every field carries a callable read + to_command and an arg kind.
        assert cmd.arg_kind in ("val", "arg")
        assert callable(cmd.read)
        assert callable(cmd.to_command)
