"""Declarative registry for the managed SDR controls (protocol half).

Extracted from custom_components/rtl_433/sdr_settings.py of
rtl-433-hass/rtl_433 (Apache-2.0). This is the *pure protocol* half of that
module: how to read each controllable rtl_433 SDR field out of the server's
``meta`` payload, which ``/cmd`` command sets it (and whether the desired value
rides on ``val`` as an integer or ``arg`` as a string), and the value transform.
The Home Assistant entity-description metadata (platform, name, native min/max/
step/unit, NumberMode, device class, EntityCategory, select options) is dropped;
only the wire-protocol contract remains.

Outbound ``/cmd`` commands follow ``WEBSOCKET_API.md`` exactly (no invented
fields):

- center frequency -> ``center_frequency``, ``val`` = Hz (live). Presented to
  the user in MHz; the registry's read/to_command convert at that boundary.
- sample rate -> ``sample_rate``, ``val`` = Hz (live).
- ppm -> ``ppm_error``, ``val`` = integer (live).
- gain -> ``gain``, ``arg`` = dB string, empty string = auto (live).
- conversion mode -> ``convert``, ``val`` = integer 0/1/2 (config-setter).
- hop interval -> ``hop_interval``, ``val`` = seconds (config-setter).

Gain is modelled as the clarified *Number (dB) + "Auto gain" Switch* pair: two
registry entries that share the ``gain`` command. A consumer stores the two
desired-state keys (``gain`` dB float, ``gain_auto`` bool) but issues exactly one
``gain`` ``/cmd`` per write, composing its ``arg`` via :func:`gain_command_arg`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# --------------------------------------------------------------------------- #
# Stable internal keys.                                                         #
# --------------------------------------------------------------------------- #
# Each key is the desired-state Store key for its field and the registry's
# stable identity token; they are referenced by the coordinator and platforms.
KEY_CENTER_FREQUENCY = "center_frequency"
KEY_SAMPLE_RATE = "sample_rate"
KEY_PPM_ERROR = "ppm_error"
KEY_GAIN_DB = "gain"
KEY_GAIN_AUTO = "gain_auto"
KEY_CONVERSION_MODE = "conversion_mode"
KEY_HOP_INTERVAL = "hop_interval"


# --------------------------------------------------------------------------- #
# Conversion-mode label <-> integer mapping.                                    #
# --------------------------------------------------------------------------- #
# The select's option index *is* the ``convert`` command ``val``, so the tuple
# order is load-bearing: native -> 0, si -> 1, customary -> 2.
CONVERSION_MODES: tuple[str, ...] = ("native", "si", "customary")


def conversion_label_to_val(label: str) -> int:
    """Map a conversion-mode label to its ``convert`` command ``val``.

    Raises ``ValueError`` for an unknown label (the select only ever offers the
    three known options, so an unknown label signals a programming error).
    """
    return CONVERSION_MODES.index(label)


def conversion_val_to_label(val: int) -> str | None:
    """Map a ``conversion_mode`` integer to its label, or ``None`` if unknown."""
    return CONVERSION_MODES[val] if 0 <= val < len(CONVERSION_MODES) else None


# --------------------------------------------------------------------------- #
# Gain command argument composer.                                               #
# --------------------------------------------------------------------------- #
def gain_command_arg(gain_db: float | None, gain_auto: bool) -> str:
    """Compose the single outbound ``gain`` ``/cmd`` ``arg`` from the gain pair.

    An empty string means auto; otherwise the dB value as a string (rtl_433
    accepts e.g. ``"32.8"``). ``%g`` trims a trailing ``.0`` so clean integers
    read as ``"40"`` rather than ``"40.0"``.
    """
    if gain_auto or gain_db is None:
        return ""
    return f"{gain_db:g}"


def _always(_meta: dict[str, Any]) -> bool:
    """Capability gate that is always satisfied (today every field is supported)."""
    return True


def frequency_count(meta: dict[str, Any]) -> int | None:
    """Number of configured frequencies, or None when unknown (pre-connect)."""
    freqs = meta.get("frequencies")
    return len(freqs) if isinstance(freqs, list) else None


def available_when_not_hopping(meta: dict[str, Any]) -> bool:
    """Center frequency is meaningful only with a single configured frequency.

    Under hop mode (more than one frequency) the API cannot represent the hop
    list and setting a single ``center_frequency`` would collapse hopping, so the
    control is hidden. Unknown frequency count (before the first connect) defaults
    to available.
    """
    count = frequency_count(meta)
    return count is None or count <= 1


def available_when_hopping(meta: dict[str, Any]) -> bool:
    """Hop interval only takes effect when more than one frequency is configured.

    With a single frequency there is nothing to hop between, so the control is
    hidden. Unknown frequency count (before the first connect) defaults to
    available.
    """
    count = frequency_count(meta)
    return count is None or count > 1


@dataclass(frozen=True, kw_only=True)
class SdrCommand:
    """One controllable SDR field, described once as a pure protocol contract.

    Pure data plus tiny callables. ``read`` extracts the current value from the
    server's ``meta`` payload; ``to_command`` maps a desired Python value to the
    value/arg actually sent on ``/cmd`` (carried as ``val`` when
    ``arg_kind == "val"``, else as ``arg``). ``capability`` gates whether the
    field is offered for a given meta (today always ``True``). ``available`` is a
    runtime-state gate: it decides whether the field is meaningful for the current
    ``meta`` -- used to hide ``hop_interval`` when the server is not hopping and
    ``center_frequency`` when it is.
    """

    key: str  # stable internal key (also the desired-state Store key)
    command: str  # /cmd command name
    arg_kind: str  # "val" (integer) | "arg" (string)

    # Read current value out of the server's meta payload.
    read: Callable[[dict[str, Any]], Any]
    # Map a desired python value -> the value/arg sent on /cmd.
    to_command: Callable[[Any], Any]

    # Capability gate (today always True; future: per-server capability).
    capability: Callable[[dict[str, Any]], bool] = field(default=_always)
    # Runtime availability gate (always available unless a field overrides it).
    available: Callable[[dict[str, Any]], bool] = field(default=_always)


# --------------------------------------------------------------------------- #
# Per-field read helpers (defensive: a missing meta key reads as None).         #
# --------------------------------------------------------------------------- #
def read_center_frequency(meta: dict[str, Any]) -> Any:
    """Read the center frequency from meta (Hz) as MHz, or None when absent.

    The wire protocol and ``meta`` keep center frequency in Hz; this is the only
    read path that converts it to the MHz the desired-state value and the control
    entity present.
    """
    hz = meta.get("center_frequency")
    if hz is None:
        return None
    try:
        return float(hz) / 1_000_000
    except TypeError, ValueError:
        return None


def read_sample_rate(meta: dict[str, Any]) -> Any:
    # The meta object names this ``samp_rate``; the registry key is sample_rate.
    return meta.get("samp_rate")


def read_ppm_error(meta: dict[str, Any]) -> Any:
    return meta.get("ppm_error")


def read_gain_db(meta: dict[str, Any]) -> float | None:
    """Read the gain dB value out of meta's gain string ("" -> None for auto)."""
    gain = meta.get("gain")
    if gain is None or gain == "":
        return None
    try:
        return float(gain)
    except TypeError, ValueError:
        return None


def read_gain_auto(meta: dict[str, Any]) -> bool | None:
    """Read whether auto gain is active ("" -> True; a value -> False)."""
    gain = meta.get("gain")
    if gain is None:
        return None
    return bool(gain == "")


def read_conversion_mode(meta: dict[str, Any]) -> int | None:
    """Read the conversion mode as the integer ``convert`` ``val``.

    The desired value is stored as the integer the ``convert`` command takes
    (and that ``meta`` natively reports); only the Select entity maps it to/from
    a human label at its UI boundary.
    """
    raw = meta.get("conversion_mode")
    if raw is None:
        return None
    try:
        return int(raw)
    except TypeError, ValueError:
        return None


def read_hop_interval(meta: dict[str, Any]) -> Any:
    # _refresh_meta exposes hop_interval (= hop_times[0]).
    return meta.get("hop_interval")


# --------------------------------------------------------------------------- #
# Outbound value transforms (desired python value -> /cmd val/arg).             #
# --------------------------------------------------------------------------- #
def int_command(value: Any) -> int:
    """Coerce a desired numeric value to the integer sent on ``val``."""
    return int(value)


def mhz_to_hz_command(value: Any) -> int:
    """Map a desired center frequency in MHz to the integer Hz ``val``.

    Inverse of :func:`read_center_frequency`. ``round`` keeps typical
    kHz-resolution frequencies exact (e.g. 433.92 MHz -> 433920000 Hz) despite
    binary-float imprecision in ``value * 1_000_000``.
    """
    return int(round(float(value) * 1_000_000))


# --------------------------------------------------------------------------- #
# The registry.                                                                 #
# --------------------------------------------------------------------------- #
SDR_COMMANDS: tuple[SdrCommand, ...] = (
    SdrCommand(
        key=KEY_CENTER_FREQUENCY,
        command="center_frequency",
        arg_kind="val",
        read=read_center_frequency,
        to_command=mhz_to_hz_command,
        # Hidden under hop mode: a single value cannot represent the hop list and
        # setting it would collapse hopping (also why adoption leaves it unmanaged).
        available=available_when_not_hopping,
    ),
    SdrCommand(
        key=KEY_SAMPLE_RATE,
        command="sample_rate",
        arg_kind="val",
        read=read_sample_rate,
        to_command=int_command,
    ),
    SdrCommand(
        key=KEY_PPM_ERROR,
        command="ppm_error",
        arg_kind="val",
        read=read_ppm_error,
        to_command=int_command,
    ),
    # --- Gain pair: a Number (dB) + a Switch ("Auto gain"), sharing "gain". --- #
    SdrCommand(
        key=KEY_GAIN_DB,
        command="gain",
        arg_kind="arg",
        read=read_gain_db,
        # The actual outbound arg is composed by gain_command_arg() from the
        # *combined* desired state; this float->str maps the dB value alone.
        to_command=lambda value: gain_command_arg(value, gain_auto=False),
    ),
    SdrCommand(
        key=KEY_GAIN_AUTO,
        command="gain",
        arg_kind="arg",
        read=read_gain_auto,
        # On -> empty arg (auto); off -> defer to the dB value at write time.
        to_command=lambda auto: gain_command_arg(None, gain_auto=bool(auto)),
    ),
    SdrCommand(
        key=KEY_CONVERSION_MODE,
        command="convert",
        arg_kind="val",
        read=read_conversion_mode,
        to_command=int_command,
    ),
    SdrCommand(
        key=KEY_HOP_INTERVAL,
        command="hop_interval",
        arg_kind="val",
        read=read_hop_interval,
        to_command=int_command,
        # Only meaningful with more than one configured frequency; hidden when the
        # server is not hopping (a single frequency has nothing to hop between).
        available=available_when_hopping,
    ),
)

# Convenience: registry indexed by stable key for O(1) lookup by consumers.
SDR_COMMANDS_BY_KEY: dict[str, SdrCommand] = {c.key: c for c in SDR_COMMANDS}
