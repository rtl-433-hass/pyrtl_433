"""Mutation-floor tests for pyrtl_433.normalizer.

Exact-value assertions that pin the character-class filter and the strip/fallback
tail of the token builder *as ``device_key`` uses it*, so small mutations
(dropping ``.`` from the safe set, ``strip("_")`` -> ``strip(None)``, mangling the
``"unknown"`` fallback) cause at least one assertion to fail.

The builder itself now lives in :mod:`pyrtl_433.naming` (as the public
:func:`~pyrtl_433.naming.safe_token`) and is pinned directly in
``test_mut_naming.py``; these assertions keep ``device_key``'s *use* of it
pinned. The behavioural key derivation (order, missing-id, whitespace/``/``
sanitising) is covered in ``test_normalizer.py``.
"""

from __future__ import annotations

from pyrtl_433.normalizer import device_key


# --------------------------------------------------------------------------- #
# safe_token: the "." is a preserved safe character.                           #
# --------------------------------------------------------------------------- #
def test_dot_is_preserved_as_a_safe_character():
    """A ``.`` in an identity value survives verbatim (kills the drop-``.`` mutant).

    ``safe_token`` keeps alnum plus ``-``, ``_``, ``.``; a mutant that removes
    ``.`` from that set would rewrite it to ``_``. Pin a dotted model so the two
    differ (``Foo.Bar`` vs ``Foo_Bar``).
    """
    assert device_key({"model": "Foo.Bar", "id": 1}) == "Foo.Bar-1"
    assert device_key({"model": "1.5", "channel": 2}) == "1.5-ch2"


# --------------------------------------------------------------------------- #
# safe_token: the trailing strip removes underscores, not whitespace.          #
# --------------------------------------------------------------------------- #
def test_leading_and_trailing_underscores_are_stripped():
    """Unsafe edge characters collapse to ``_`` and are then stripped off.

    A value wrapped in ``/`` becomes ``_x_`` inside the token, and the final
    ``strip("_")`` must remove those edge underscores -> ``x``. A mutant using
    ``strip(None)`` would strip whitespace instead and leave ``_x_``.
    """
    assert device_key({"model": "/x/", "id": 7}) == "x-7"
    # Interior underscores are preserved; only the edges are stripped.
    assert device_key({"model": "/a_b/", "id": 1}) == "a_b-1"


# --------------------------------------------------------------------------- #
# safe_token: the empty-token fallback is exactly "unknown".                   #
# --------------------------------------------------------------------------- #
def test_all_unsafe_value_falls_back_to_unknown():
    """A value with no safe characters yields the ``"unknown"`` token exactly.

    ``"/"`` -> ``"_"`` -> ``strip("_")`` -> ``""`` -> the ``or "unknown"`` fallback.
    Kills mutants that mangle the fallback literal (``"XXunknownXX"`` / ``"UNKNOWN"``).
    This exercises ``safe_token``'s own fallback (not ``device_key``'s
    model-is-None ``"unknown"``), by passing a present-but-unsafe model.
    """
    assert device_key({"model": "/", "id": 5}) == "unknown-5"
    assert device_key({"model": "#", "channel": 1}) == "unknown-ch1"


# --------------------------------------------------------------------------- #
# Documented EQUIVALENT mutant on safe_token (not forced).                     #
# --------------------------------------------------------------------------- #
# One surviving ``safe_token`` mutant is genuinely equivalent: dropping ``"_"``
# from the safe-character tuple (``("-", "_", ".")`` -> ``("-", "XX_XX", ".")``).
# A literal underscore that is no longer "safe" falls to the ``else``
# branch, which appends ``"_"`` -- the exact same character it would have appended
# as a safe char. Every input therefore produces an identical token, so no
# assertion can distinguish it (the same equivalent-mutant class the parent's
# normalizer floor documents). Recorded here rather than suppressed.
