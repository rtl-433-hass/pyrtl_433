"""Mutation-floor tests for pyrtl_433.sdr.

Ported from tests/test_mut_sdr_settings_floor.py of rtl-433-hass/rtl_433
(Apache-2.0), rewritten against the slimmed ``SdrCommand`` registry. These assert
exact, low-level behaviour so small mutations (wrong operator, wrong dict key,
wrong comparison string, wrong rounding) cause at least one assertion to fail.

Groups:
- conversion_val_to_label -- boundary at val=0 and out-of-range val=3
- read_gain_db (via SdrCommand.read) -- correct dict key, guard semantics,
  empty-string -> None, non-empty -> float
- read_gain_auto (via SdrCommand.read) -- correct dict key, None -> None,
  empty-string -> True, non-empty -> False
- read_conversion_mode / mhz_to_hz_command / int_command exactness
"""

from __future__ import annotations

import pytest

from pyrtl_433.sdr import (
    CONVERSION_MODES,
    KEY_CONVERSION_MODE,
    KEY_GAIN_AUTO,
    KEY_GAIN_DB,
    KEY_HOP_INTERVAL,
    KEY_PPM_ERROR,
    KEY_SAMPLE_RATE,
    SDR_COMMANDS_BY_KEY,
    conversion_val_to_label,
    int_command,
    mhz_to_hz_command,
)


# --------------------------------------------------------------------------- #
# conversion_val_to_label: boundary tests                                      #
# --------------------------------------------------------------------------- #
class TestConversionValToLabel:
    """Exact return values for every in-range index and None for out-of-range."""

    def test_zero_returns_native(self):
        """val=0 -> 'native' (kills mutants that shift the lower bound)."""
        result = conversion_val_to_label(0)
        assert result == "native"
        assert result == CONVERSION_MODES[0]

    def test_one_returns_si(self):
        """val=1 -> 'si'."""
        assert conversion_val_to_label(1) == "si"

    def test_two_returns_customary(self):
        """val=2 -> 'customary'."""
        assert conversion_val_to_label(2) == "customary"

    def test_three_returns_none(self):
        """val=3 == len(CONVERSION_MODES) -> None (not an IndexError).

        Kills the mutant that uses ``<= len(...)`` and would index 3.
        """
        assert conversion_val_to_label(3) is None

    def test_negative_returns_none(self):
        """val=-1 < 0 -> None."""
        assert conversion_val_to_label(-1) is None

    def test_large_out_of_range_returns_none(self):
        """val=100 -> None."""
        assert conversion_val_to_label(100) is None


# --------------------------------------------------------------------------- #
# read_gain_db: exercised via SDR_COMMANDS_BY_KEY[KEY_GAIN_DB].read            #
# --------------------------------------------------------------------------- #
class TestReadGainDb:
    """read_gain_db via the registry SdrCommand.read callable."""

    @pytest.fixture(autouse=True)
    def _setting(self):
        self.read = SDR_COMMANDS_BY_KEY[KEY_GAIN_DB].read

    def test_gain_string_returns_float(self):
        """meta['gain']='32.8' -> 32.8 (kills key-mutation and float(None) mutants)."""
        result = self.read({"gain": "32.8"})
        assert result == pytest.approx(32.8)
        assert isinstance(result, float)

    def test_gain_integer_string_returns_float(self):
        """meta['gain']='40' -> 40.0."""
        result = self.read({"gain": "40"})
        assert result == pytest.approx(40.0)
        assert isinstance(result, float)

    def test_empty_string_returns_none(self):
        """meta['gain']='' -> None (auto-gain sentinel)."""
        assert self.read({"gain": ""}) is None

    def test_missing_key_returns_none(self):
        """meta with no 'gain' key -> None."""
        assert self.read({}) is None

    def test_none_value_returns_none(self):
        """meta['gain']=None -> None."""
        assert self.read({"gain": None}) is None

    def test_non_gain_key_is_ignored(self):
        """A meta that has only a different key returns None (kills wrong-key mutants)."""
        assert self.read({"GAIN": "32.8"}) is None
        assert self.read({"XXgainXX": "32.8"}) is None

    def test_non_empty_string_not_suppressed_by_empty_check(self):
        """A non-empty gain string is NOT None (kills ``!= ""`` flip)."""
        result = self.read({"gain": "32.8"})
        assert result is not None
        assert result == pytest.approx(32.8)

    def test_empty_string_suppressed_correctly(self):
        """Empty string IS None but a value string is NOT (kills or/and flip)."""
        assert self.read({"gain": ""}) is None
        assert self.read({"gain": "10.5"}) is not None

    def test_invalid_string_returns_none(self):
        """A non-numeric string is caught by the except clause -> None."""
        assert self.read({"gain": "auto"}) is None


# --------------------------------------------------------------------------- #
# read_gain_auto: exercised via SDR_COMMANDS_BY_KEY[KEY_GAIN_AUTO].read        #
# --------------------------------------------------------------------------- #
class TestReadGainAuto:
    """read_gain_auto via the registry SdrCommand.read callable."""

    @pytest.fixture(autouse=True)
    def _setting(self):
        self.read = SDR_COMMANDS_BY_KEY[KEY_GAIN_AUTO].read

    def test_missing_gain_key_returns_none(self):
        """meta with no 'gain' key -> None (no gain info at all)."""
        assert self.read({}) is None

    def test_none_gain_value_returns_none(self):
        """meta['gain']=None -> None (pre-connect / unavailable)."""
        assert self.read({"gain": None}) is None

    def test_empty_string_returns_true(self):
        """meta['gain']='' -> True (auto gain is active)."""
        assert self.read({"gain": ""}) is True

    def test_nonempty_gain_returns_false(self):
        """meta['gain']='32.8' -> False (manual gain, auto is off)."""
        assert self.read({"gain": "32.8"}) is False

    def test_nonempty_gain_zero_string_returns_false(self):
        """meta['gain']='0' -> False (0 dB manual gain; non-empty string)."""
        assert self.read({"gain": "0"}) is False

    def test_non_gain_key_is_ignored(self):
        """A meta without the 'gain' key returns None (kills wrong-key mutants)."""
        assert self.read({"GAIN": ""}) is None
        assert self.read({"XXgainXX": ""}) is None

    def test_empty_vs_nonempty_are_distinct(self):
        """Empty -> True, non-empty -> False: the two branches are distinct."""
        assert self.read({"gain": ""}) is True
        assert self.read({"gain": "10"}) is False
        assert self.read({"gain": "32.8"}) is False


# --------------------------------------------------------------------------- #
# read_conversion_mode: correct key, int coercion, guard semantics.            #
# --------------------------------------------------------------------------- #
class TestReadConversionMode:
    """read_conversion_mode via the registry SdrCommand.read callable."""

    @pytest.fixture(autouse=True)
    def _setting(self):
        self.read = SDR_COMMANDS_BY_KEY[KEY_CONVERSION_MODE].read

    def test_reads_correct_key_as_int(self):
        """meta['conversion_mode']=1 -> 1 (int)."""
        result = self.read({"conversion_mode": 1})
        assert result == 1
        assert isinstance(result, int)

    def test_numeric_string_is_coerced(self):
        """meta['conversion_mode']='2' -> 2 (int coercion)."""
        assert self.read({"conversion_mode": "2"}) == 2

    def test_missing_key_returns_none(self):
        """No conversion_mode key -> None (kills wrong-key mutants)."""
        assert self.read({}) is None
        assert self.read({"CONVERSION": 1}) is None

    def test_none_value_returns_none(self):
        """meta['conversion_mode']=None -> None."""
        assert self.read({"conversion_mode": None}) is None

    def test_uncoercible_value_returns_none(self):
        """A non-numeric value is caught by the except clause -> None."""
        assert self.read({"conversion_mode": "native"}) is None


# --------------------------------------------------------------------------- #
# mhz_to_hz_command / int_command: exact rounding + coercion.                  #
# --------------------------------------------------------------------------- #
class TestOutboundTransforms:
    """Exact-value pins on the outbound value transforms."""

    def test_mhz_to_hz_is_rounded_not_truncated(self):
        """433.92 MHz -> 433920000 Hz exactly (round(), not int() truncation).

        Kills a mutant that drops ``round`` and truncates the binary-float
        product (433.92 * 1e6 == 433919999.99999994 -> 433919999 truncated).
        """
        assert mhz_to_hz_command(433.92) == 433_920_000
        assert mhz_to_hz_command(915.0) == 915_000_000
        assert mhz_to_hz_command(868.3) == 868_300_000
        assert isinstance(mhz_to_hz_command(433.92), int)

    def test_int_command_truncates_toward_zero(self):
        """int_command uses int(): 3.9 -> 3 (not rounded to 4)."""
        assert int_command(3.9) == 3
        assert int_command(2.0) == 2
        assert isinstance(int_command(2.0), int)


# --------------------------------------------------------------------------- #
# Plain meta.get(<key>) readers: exact key, correct value pass-through.        #
# --------------------------------------------------------------------------- #
class TestPlainMetaReaders:
    """read_sample_rate / read_ppm_error / read_hop_interval read the right key.

    Each is a bare ``meta.get("<key>")``; the mutation set flips the key to
    ``None`` / a mangled string / an upper-cased string. Reading a value back out
    under the real key (and ``None`` for a wrong/absent key) fails the moment the
    key is mutated, so these pin the exact ``meta`` field each control samples.
    """

    def test_read_sample_rate_uses_samp_rate_key(self):
        """SAMPLE_RATE reads meta['samp_rate'] verbatim (note the wire spelling)."""
        read = SDR_COMMANDS_BY_KEY[KEY_SAMPLE_RATE].read
        assert read({"samp_rate": 250000}) == 250000
        assert read({}) is None
        # Wrong-key mutants (None / "XXsamp_rateXX" / "SAMP_RATE") read nothing here.
        assert read({"SAMP_RATE": 250000}) is None
        assert read({"sample_rate": 250000}) is None

    def test_read_ppm_error_uses_ppm_error_key(self):
        """PPM_ERROR reads meta['ppm_error'] verbatim, including 0."""
        read = SDR_COMMANDS_BY_KEY[KEY_PPM_ERROR].read
        assert read({"ppm_error": 2}) == 2
        assert read({"ppm_error": 0}) == 0
        assert read({}) is None
        assert read({"PPM_ERROR": 2}) is None

    def test_read_hop_interval_uses_hop_interval_key(self):
        """HOP_INTERVAL reads meta['hop_interval'] verbatim."""
        read = SDR_COMMANDS_BY_KEY[KEY_HOP_INTERVAL].read
        assert read({"hop_interval": 600}) == 600
        assert read({}) is None
        assert read({"HOP_INTERVAL": 600}) is None


# --------------------------------------------------------------------------- #
# Capability / availability default gate (``_always``).                        #
# --------------------------------------------------------------------------- #
class TestAlwaysGate:
    """The default capability/availability gate returns True for any meta.

    Every command's ``capability`` defaults to ``_always`` (and the fields with no
    availability override use it for ``available`` too). Pinning both to ``True``
    kills the ``_always`` mutant that flips the default to ``False`` (which would
    hide every managed control).
    """

    def test_default_capability_is_always_true(self):
        cmd = SDR_COMMANDS_BY_KEY[KEY_SAMPLE_RATE]
        assert cmd.capability({}) is True
        assert cmd.capability({"anything": 1}) is True

    def test_default_availability_is_always_true(self):
        # sample_rate / ppm_error carry no availability override, so their
        # ``available`` is the ``_always`` default.
        assert SDR_COMMANDS_BY_KEY[KEY_SAMPLE_RATE].available({}) is True
        assert SDR_COMMANDS_BY_KEY[KEY_PPM_ERROR].available({"frequencies": []}) is True


# --------------------------------------------------------------------------- #
# Documented EQUIVALENT mutants on read_gain_db (not forced).                  #
# --------------------------------------------------------------------------- #
# Two surviving mutants on ``read_gain_db`` are genuinely equivalent because the
# ``except (TypeError, ValueError)`` fall-through returns the same ``None`` the
# early guard returns:
#
#   * ``if gain is None or gain == "":`` -> ``... and gain == "":`` (mutmut_5).
#     For ``gain is None`` the guard no longer short-circuits, so ``float(None)``
#     raises ``TypeError`` -> caught -> ``None``. For ``gain == ""`` the guard no
#     longer short-circuits, so ``float("")`` raises ``ValueError`` -> caught ->
#     ``None``. Every input yields the identical result, so no assertion can
#     distinguish it (matching the parent's documented equivalent-mutant class).
#   * ``gain == ""`` -> ``gain == "XXXX"`` (mutmut_8). ``""`` now misses the guard
#     and reaches ``float("")`` -> ``ValueError`` -> ``None``; a literal ``"XXXX"``
#     would be caught either by the (dead) guard or by ``float`` -> ``None``. The
#     observable return is unchanged for every input, so it is equivalent.
# These are recorded here (never suppressed) rather than chased with a contrived
# assertion; ``read_gain_db``'s live behaviour is fully pinned by TestReadGainDb.
