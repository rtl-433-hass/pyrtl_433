"""Tests for the event-driven device classifier (``pyrtl_433.availability``).

The two helpers are pure set algebra, but the *edges* are where the availability
bug they were extracted from lived: an event device that had been silent since a
restart was classified from its live payload alone (empty), fell back to the
periodic default, and expired its battery and other sensors to unavailable.

So these pin the three things a consumer's timeout policy depends on:

* an empty ``event_driven_keys`` degrades to "not event-driven" (a failed or
  empty library load must not pin every device to never-expire),
* the union is restart-safe: either side may be ``None`` and the adopted side
  alone is enough to classify, and
* the returned set is a fresh object the caller may mutate.
"""

from __future__ import annotations

import pytest

from pyrtl_433.availability import is_event_driven, known_field_keys
from pyrtl_433.library import event_driven_field_keys, load_library


# --------------------------------------------------------------------------- #
# is_event_driven                                                             #
# --------------------------------------------------------------------------- #
def test_intersecting_key_is_event_driven():
    """Any one matching key marks the whole device event-driven."""
    assert is_event_driven({"battery_ok", "contact_open"}, {"contact_open", "motion"})


def test_disjoint_keys_are_not_event_driven():
    """A periodic reporter (temperature, humidity, battery) is not event-driven."""
    assert not is_event_driven(
        {"temperature_C", "humidity", "battery_ok"}, {"contact_open", "motion"}
    )


def test_empty_event_driven_keys_is_false():
    """An empty key set degrades to the periodic default, never never-expire.

    An empty set is what a failed or empty library load produces. Returning
    ``True`` there would silently stop every device from ever expiring.
    """
    assert not is_event_driven({"motion"}, set())
    assert not is_event_driven({"motion"}, frozenset())
    assert not is_event_driven(set(), set())


def test_empty_field_keys_is_false():
    """A device with no known fields at all gets the periodic default."""
    assert not is_event_driven(set(), {"motion"})
    assert not is_event_driven([], {"motion"})


def test_accepts_any_iterable_and_collection():
    """``field_keys`` may be any iterable; ``event_driven_keys`` any collection."""
    assert is_event_driven(iter(["motion"]), frozenset({"motion"}))
    assert is_event_driven(["motion", "battery_ok"], ["motion"])
    assert is_event_driven(("motion",), ("motion", "button"))


def test_classifies_against_the_shipped_library():
    """End-to-end with the real library's event-driven key set.

    ``contact_open`` (``event_driven: true``) and ``button``
    (``platform: event``) are event-driven; ``temperature_C`` is not.
    """
    registry, _ = load_library()
    keys = event_driven_field_keys(registry)

    assert is_event_driven({"contact_open", "battery_ok"}, keys)
    assert is_event_driven({"button"}, keys)
    assert not is_event_driven({"temperature_C", "humidity", "battery_ok"}, keys)


# --------------------------------------------------------------------------- #
# known_field_keys                                                            #
# --------------------------------------------------------------------------- #
def test_unions_adopted_and_live():
    """Both sides contribute; duplicates collapse."""
    assert known_field_keys(["battery_ok", "motion"], ["battery_ok", "rssi"]) == {
        "battery_ok",
        "motion",
        "rssi",
    }


@pytest.mark.parametrize(
    "adopted, live, expected",
    [
        (None, None, set()),
        (None, ["motion"], {"motion"}),
        (["motion"], None, {"motion"}),
        ([], [], set()),
    ],
)
def test_either_side_may_be_none_or_empty(adopted, live, expected):
    """A device with nothing persisted, or nothing seen this session, still works."""
    assert known_field_keys(adopted, live) == expected


def test_adopted_alone_classifies_a_device_silent_since_restart():
    """The restart-safe half: adopted fields alone mark a device event-driven.

    This is the regression the union exists for — reading only the live payload
    (empty after a restart, before the device next transmits) left an event
    device on the periodic default and expired its battery sensor.
    """
    keys = known_field_keys(["contact_open", "battery_ok"], None)
    assert is_event_driven(keys, {"contact_open", "motion"})


def test_returns_a_fresh_mutable_set():
    """The result is a new ``set``, not a view onto either argument."""
    adopted = {"battery_ok"}
    live = {"motion"}
    result = known_field_keys(adopted, live)

    assert isinstance(result, set)
    result.add("extra")
    assert adopted == {"battery_ok"}
    assert live == {"motion"}


def test_accepts_iterators():
    """Either side may be a one-shot iterator."""
    assert known_field_keys(iter(["a"]), iter(["b"])) == {"a", "b"}
