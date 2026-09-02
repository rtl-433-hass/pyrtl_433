"""Tests for the device-naming helpers.

Covers the token builder (:func:`safe_token`) and the two key-shape helpers
built on it: :func:`display_name` (model plus its distinguishing id suffix) and
:func:`identity_suffix` (that suffix alone, for a serial number).

Ported from tests/test_normalizer.py and tests/test_mut_entity.py of
rtl-433-hass/rtl_433 (Apache-2.0), whose ``_safe_token`` /
``_device_display_name`` / ``_device_identity`` these replace: the outputs are a
frozen contract (consumers embed them in stable identifiers), so the cases are
kept as exact-value assertions.
"""

from __future__ import annotations

import pytest

import pyrtl_433
from pyrtl_433.naming import display_name, identity_suffix, safe_token
from pyrtl_433.normalizer import _safe_token, device_key


# --------------------------------------------------------------------------- #
# safe_token                                                                   #
# --------------------------------------------------------------------------- #
def test_safe_token_is_deterministic_and_preserves_safe_characters():
    """Alphanumerics plus ``-``/``_``/``.`` survive verbatim, every time."""
    assert safe_token("Acurite-606TX") == "Acurite-606TX"
    assert safe_token("Acurite-606TX") == safe_token("Acurite-606TX")
    assert safe_token("v1.2_beta") == "v1.2_beta"
    # Non-string identity values are stringified.
    assert safe_token(42) == "42"


def test_safe_token_collapses_unsafe_characters_to_underscore():
    """Whitespace and ``/`` become underscores; edge underscores are stripped."""
    assert safe_token("Brand X/Model 1") == "Brand_X_Model_1"
    # Leading/trailing whitespace is stripped rather than turned into "_".
    assert safe_token("  spaced  ") == "spaced"
    assert safe_token("a//b") == "a__b"


def test_safe_token_empty_or_all_unsafe_falls_back_to_unknown():
    """A value with nothing safe left yields the ``"unknown"`` token."""
    assert safe_token("") == "unknown"
    assert safe_token("   ") == "unknown"
    assert safe_token("/ /") == "unknown"


def test_safe_token_is_exported_from_the_package_root():
    """The naming helpers are part of the public API surface."""
    assert pyrtl_433.safe_token is safe_token
    assert pyrtl_433.display_name is display_name
    assert pyrtl_433.identity_suffix is identity_suffix


def test_normalizer_private_alias_still_resolves():
    """``normalizer._safe_token`` stays a working alias for the public helper.

    The token builder used to live in ``normalizer`` under the private name, and
    consumers imported it from there; the alias keeps those imports working.
    """
    assert _safe_token is safe_token


# --------------------------------------------------------------------------- #
# display_name / identity_suffix, over the key shapes device_key produces      #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("model", "key", "expected_name", "expected_identity"),
    [
        pytest.param(
            "Fineoffset-WH51",
            "Fineoffset-WH51-00c50f",
            "Fineoffset-WH51 00c50f",
            "00c50f",
            id="model-and-id",
        ),
        pytest.param(
            "Acurite-986",
            "Acurite-986-1a2b-ch2",
            "Acurite-986 1a2b-ch2",
            "1a2b-ch2",
            id="model-id-and-channel",
        ),
        pytest.param(
            "Foo",
            "Foo-5-ch1-st2",
            "Foo 5-ch1-st2",
            "5-ch1-st2",
            id="model-id-channel-and-subtype",
        ),
        pytest.param(
            "Foo",
            "Foo-ch3",
            "Foo ch3",
            "ch3",
            id="model-and-channel-only",
        ),
        pytest.param("Foo", "Foo", "Foo", None, id="model-only-key"),
        pytest.param(
            "",
            "UnknownDevice-7",
            "UnknownDevice-7",
            None,
            id="no-model-falls-back-to-key",
        ),
        pytest.param("", "unknown", "unknown", None, id="no-model-unknown-key"),
        pytest.param(
            "Acurite-986",
            "Nexus-TH-77",
            "Acurite-986",
            None,
            id="key-not-under-model",
        ),
        pytest.param(
            "Brand X/Model 1",
            "Brand_X_Model_1-7",
            "Brand X/Model 1 7",
            "7",
            id="model-tokenized-before-stripping",
        ),
        pytest.param(
            "/ /",
            "unknown-5",
            "/ / 5",
            "5",
            id="all-unsafe-model-uses-unknown-token",
        ),
    ],
)
def test_display_name_and_identity_suffix(model, key, expected_name, expected_identity):
    """The name folds in only the id suffix; the suffix is published on its own."""
    assert display_name(model, key) == expected_name
    assert identity_suffix(model, key) == expected_identity


def test_helpers_agree_with_the_keys_device_key_produces():
    """The helpers undo exactly the prefix ``device_key`` puts on.

    Round-tripping through :func:`~pyrtl_433.normalizer.device_key` keeps the two
    sides of the ``<model-token>-<id>[-ch..][-st..]`` contract in step, including
    for a model whose token differs from the model string.
    """
    event = {"model": "Brand X/Model 1", "id": 42, "channel": 3, "subtype": 2}
    key = device_key(event)
    assert key == "Brand_X_Model_1-42-ch3-st2"
    assert identity_suffix(event["model"], key) == "42-ch3-st2"
    assert display_name(event["model"], key) == "Brand X/Model 1 42-ch3-st2"

    model_only = {"model": "OnlyModel"}
    key = device_key(model_only)
    assert display_name(model_only["model"], key) == "OnlyModel"
    assert identity_suffix(model_only["model"], key) is None
