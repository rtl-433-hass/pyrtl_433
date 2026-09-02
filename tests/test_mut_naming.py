"""Mutation-floor tests for pyrtl_433.naming.

Exact-value assertions that pin the branches the behavioural tests in
``test_naming.py`` only reach incidentally: the character-class filter and the
strip/fallback tail of :func:`safe_token` (asserted directly here rather than
through :func:`~pyrtl_433.normalizer.device_key`, as ``test_mut_normalizer.py``
does), the ``<model-token>-`` prefix the suffix helpers strip, and the
empty-suffix half of the ``not suffix or suffix == device_key`` guard — a key
shape ``device_key`` itself never emits, so nothing else covers it.

These outputs are a frozen contract (consumers embed them in stable
identifiers), so every assertion here is an exact value, never a shape check.
"""

from __future__ import annotations

from pyrtl_433.naming import display_name, identity_suffix, safe_token


# --------------------------------------------------------------------------- #
# safe_token: the "." is a preserved safe character.                           #
# --------------------------------------------------------------------------- #
def test_dot_and_dash_are_preserved_as_safe_characters():
    """``.`` and ``-`` survive verbatim (kills the drop-a-safe-char mutants).

    ``safe_token`` keeps alnum plus ``-``, ``_``, ``.``; a mutant that removes
    one of those from the set would rewrite it to ``_``, so pin values where the
    two differ (``Foo.Bar`` vs ``Foo_Bar``).
    """
    assert safe_token("Foo.Bar") == "Foo.Bar"
    assert safe_token("1.5") == "1.5"
    assert safe_token("a-b") == "a-b"


# --------------------------------------------------------------------------- #
# safe_token: the trailing strip removes underscores, not whitespace.          #
# --------------------------------------------------------------------------- #
def test_edge_underscores_are_stripped_but_interior_ones_survive():
    """A value wrapped in ``/`` loses its edge underscores, keeps interior ones.

    ``/x/`` becomes ``_x_``, and the final ``strip("_")`` must remove those edge
    underscores -> ``x``. A mutant using ``strip(None)`` would strip whitespace
    instead and leave ``_x_``; one stripping interior characters too would
    mangle ``a_b``.
    """
    assert safe_token("/x/") == "x"
    assert safe_token("/a_b/") == "a_b"


# --------------------------------------------------------------------------- #
# safe_token: the empty-token fallback is exactly "unknown".                   #
# --------------------------------------------------------------------------- #
def test_all_unsafe_value_falls_back_to_the_exact_unknown_literal():
    """A value with no safe characters yields the ``"unknown"`` token exactly.

    ``"/"`` -> ``"_"`` -> ``strip("_")`` -> ``""`` -> the ``or "unknown"``
    fallback. Kills mutants that mangle the fallback literal
    (``"XXunknownXX"`` / ``"UNKNOWN"``).
    """
    assert safe_token("/") == "unknown"
    assert safe_token("#") == "unknown"


# --------------------------------------------------------------------------- #
# The stripped prefix is the model TOKEN plus a literal "-".                   #
# --------------------------------------------------------------------------- #
def test_prefix_stripped_is_the_model_token_and_a_dash():
    """The prefix is ``safe_token(model)`` + ``"-"``, not the raw model.

    A model containing unsafe characters is tokenized the same way
    ``device_key`` tokenized it, so the prefix matches and only the id is left.
    A mutant that skips the tokenization, or mangles the ``"-"`` separator,
    fails to strip anything and would return the whole key.
    """
    assert identity_suffix("Brand X/Model 1", "Brand_X_Model_1-7") == "7"
    assert display_name("Brand X/Model 1", "Brand_X_Model_1-7") == "Brand X/Model 1 7"
    # Only the first occurrence of the prefix is removed; the rest is the suffix.
    assert identity_suffix("Foo", "Foo-Foo-1") == "Foo-1"


# --------------------------------------------------------------------------- #
# The empty-suffix half of the "no distinguishing suffix" guard.               #
# --------------------------------------------------------------------------- #
def test_key_that_is_just_the_model_token_and_a_dash_has_no_suffix():
    """A key ending at the separator leaves nothing to distinguish a unit.

    ``device_key`` never emits ``"Foo-"``, so only this test covers the ``not
    suffix`` half of the guard: without it a mutant dropping that check (or
    turning the ``or`` into an ``and``) could publish an empty serial number and
    a name with a trailing space.
    """
    assert identity_suffix("Foo", "Foo-") is None
    assert display_name("Foo", "Foo-") == "Foo"


# --------------------------------------------------------------------------- #
# The name separator is a single space, and the model is not duplicated.       #
# --------------------------------------------------------------------------- #
def test_display_name_joins_model_and_suffix_with_one_space():
    """``"<model> <suffix>"`` exactly — no duplicated model, no other separator.

    Pins the f-string: a mutant altering the literal space (or emitting the
    whole key after the model) changes this exact string.
    """
    assert display_name("Fineoffset-WH51", "Fineoffset-WH51-00c50f") == (
        "Fineoffset-WH51 00c50f"
    )
    assert display_name("Acurite-986", "Acurite-986-1a2b-ch2") == "Acurite-986 1a2b-ch2"


# --------------------------------------------------------------------------- #
# The falsy-model branch returns the key / None, not the model.                #
# --------------------------------------------------------------------------- #
def test_missing_model_returns_the_raw_key_and_no_identity():
    """An empty model short-circuits both helpers (kills the inverted guard).

    A mutant flipping ``if not model`` would take the strip path instead, which
    for ``""`` strips a bare ``"-"`` prefix and returns a different value.
    """
    assert display_name("", "UnknownDevice-7") == "UnknownDevice-7"
    assert identity_suffix("", "UnknownDevice-7") is None
    # Even for a key that starts with the "unknown" token safe_token("") emits.
    assert display_name("", "unknown-ch1") == "unknown-ch1"
    assert identity_suffix("", "unknown-ch1") is None


# --------------------------------------------------------------------------- #
# Documented EQUIVALENT mutant on safe_token (not forced).                     #
# --------------------------------------------------------------------------- #
# One surviving ``safe_token`` mutant is genuinely equivalent: dropping ``"_"``
# from the safe-character tuple (``("-", "_", ".")`` -> ``("-", "XX_XX", ".")``).
# A literal underscore that is no longer "safe" falls to the ``else`` branch,
# which appends ``"_"`` -- the exact same character it would have appended as a
# safe char. Every input therefore produces an identical token, so no assertion
# can distinguish it. Recorded here rather than suppressed (the same
# equivalent-mutant class ``test_mut_normalizer.py`` documented while this code
# still lived in ``normalizer.py``).
