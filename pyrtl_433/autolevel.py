"""Parse rtl_433 "Auto Level" log messages into structured noise readings.

rtl_433 exposes no structured noise-floor data anywhere in its API — not in
``get_stats``, not in any ``/cmd`` getter, not in the ``/metrics`` endpoint.
The receiver's noise estimate surfaces only as pulse-detector log lines (log
source ``"Auto Level"``), which the server forwards to every HTTP/WebSocket
client as ``{"time", "src", "lvl", "msg"}`` JSON frames (rtl_433 >= 23.11,
structured log redirect). Two message forms exist, both emitted at
``LOG_WARNING`` — the default verbosity, so they reach clients without ``-v``:

- ``Estimated noise level is -38.4 dB, adjusting minimum detection level to
  -35.4 dB`` — emitted by ``-Y autolevel`` whenever the estimate moves by more
  than 1 dB (``src/r_flow.c`` upstream; ``src/rtl_433.c`` before 26.x).
- ``Current noise level -38.2 dB, estimated noise -38.4 dB`` (or ``Current
  signal level …`` while a transmission is in progress) — emitted periodically
  by ``-M noise[:secs]`` (default every 10 s).

This module owns the deliberately narrow text parsing: the regexes anchor on
the exact upstream wording (unchanged since release 21.12) and a non-matching
message parses to ``None`` — the consumer's reading simply does not update, so
an upstream wording change degrades to "no data", never to wrong data. The
instantaneous ``Current …`` level is deliberately not surfaced: it flip-flops
between noise and in-transmission signal strength; the smoothed *estimated
noise* is the stable measurement both message forms agree on.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

# The rtl_433 log source string of the pulse-detector auto-level feature; a
# consumer should only attempt to parse messages carrying this ``src``.
AUTO_LEVEL_SRC = "Auto Level"

# ``%.1f dB`` as printed by upstream (a plain float, optionally signed).
_DB = r"(-?\d+(?:\.\d+)?) dB"

# "-Y autolevel" adjustment message: estimated noise + new minimum level.
_ESTIMATED_RE = re.compile(
    rf"^Estimated noise level is {_DB}, adjusting minimum detection level to {_DB}$"
)

# "-M noise[:secs]" periodic report: instantaneous noise|signal + estimated noise.
_CURRENT_RE = re.compile(
    rf"^Current (?:noise|signal) level {_DB}, estimated noise {_DB}$"
)


@dataclass(frozen=True, kw_only=True)
class AutoLevelReading:
    """One parsed "Auto Level" log message.

    ``noise_db`` is the receiver's estimated noise level (dB, present in both
    message forms); ``min_level_db`` is the auto-adjusted minimum detection
    level (dB, only in the ``-Y autolevel`` adjustment form).
    """

    noise_db: float
    min_level_db: float | None = None


def parse_auto_level(msg: str) -> AutoLevelReading | None:
    """Parse one ``src == "Auto Level"`` log message; ``None`` when unrecognized.

    Only the two known upstream message forms parse; anything else — including
    a future wording change — returns ``None`` so a consumer never stores a
    misread value.
    """
    match = _ESTIMATED_RE.match(msg)
    if match is not None:
        return AutoLevelReading(
            noise_db=float(match.group(1)), min_level_db=float(match.group(2))
        )
    match = _CURRENT_RE.match(msg)
    if match is not None:
        return AutoLevelReading(noise_db=float(match.group(2)))
    return None
