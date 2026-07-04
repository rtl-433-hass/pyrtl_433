"""Tests for the URL builders and getter-response unwrapping.

Fresh tests mirroring the coordinator-base cases in
tests/test_mut_coordinator_base.py of rtl-433-hass/rtl_433 (Apache-2.0),
rewritten to call :mod:`pyrtl_433._urls` directly.
"""

from __future__ import annotations

from pyrtl_433._urls import build_cmd_url, build_ws_url, unwrap_result


# --------------------------------------------------------------------------- #
# build_ws_url                                                                 #
# --------------------------------------------------------------------------- #
def test_build_ws_url_plain():
    """ws:// URL with a path already starting with /."""
    assert build_ws_url("host", 8433, "/ws") == "ws://host:8433/ws"


def test_build_ws_url_no_leading_slash():
    """A path without a leading / gets one prepended."""
    assert build_ws_url("host", 8433, "ws") == "ws://host:8433/ws"


def test_build_ws_url_secure():
    """secure=True produces wss:// not ws://."""
    url = build_ws_url("host", 8433, "/ws", secure=True)
    assert url == "wss://host:8433/ws"
    assert url.startswith("wss://")


def test_build_ws_url_secure_false():
    """secure=False produces ws:// (explicit False, not default)."""
    url = build_ws_url("host", 9000, "/events", secure=False)
    assert url == "ws://host:9000/events"
    assert not url.startswith("wss://")


def test_build_ws_url_contains_path():
    """The configured path is present in the URL verbatim."""
    assert "/mypath" in build_ws_url("host", 1234, "/mypath")


# --------------------------------------------------------------------------- #
# build_cmd_url                                                                #
# --------------------------------------------------------------------------- #
def test_build_cmd_url_plain():
    """http:// URL always points to /cmd at server root."""
    assert build_cmd_url("rtl433.local", 8433) == "http://rtl433.local:8433/cmd"


def test_build_cmd_url_secure():
    """secure=True switches to https://."""
    url = build_cmd_url("rtl433.local", 8433, secure=True)
    assert url == "https://rtl433.local:8433/cmd"
    assert url.startswith("https://")


def test_build_cmd_url_always_ends_with_cmd():
    """The cmd URL always ends with /cmd regardless of scheme."""
    assert build_cmd_url("host", 8433).endswith("/cmd")
    assert build_cmd_url("host", 8433, secure=True).endswith("/cmd")


# --------------------------------------------------------------------------- #
# unwrap_result                                                                #
# --------------------------------------------------------------------------- #
def test_unwrap_result_with_result_key():
    """A {'result': value} envelope is unwrapped to the inner value."""
    assert unwrap_result({"result": 42}) == 42


def test_unwrap_result_with_result_none():
    """{'result': None} unwraps to None (not the dict itself)."""
    assert unwrap_result({"result": None}) is None


def test_unwrap_result_bare_dict_no_result_key():
    """A dict without 'result' is returned as-is (same object)."""
    d = {"other": "value"}
    assert unwrap_result(d) is d


def test_unwrap_result_bare_string():
    """A bare string is returned as-is."""
    assert unwrap_result("bare") == "bare"


def test_unwrap_result_bare_int():
    """A bare integer is returned as-is."""
    assert unwrap_result(99) == 99


def test_unwrap_result_none():
    """None is returned as-is (not treated as a result envelope)."""
    assert unwrap_result(None) is None


def test_unwrap_result_string_gain():
    """A gain string in a result envelope is unwrapped correctly."""
    assert unwrap_result({"result": "32.8"}) == "32.8"


def test_unwrap_result_empty_string_gain():
    """Empty string gain (auto) in a result envelope is unwrapped correctly."""
    assert unwrap_result({"result": ""}) == ""
