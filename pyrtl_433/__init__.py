"""pyrtl_433 -- a standalone async client for the rtl_433 WebSocket/HTTP API.

The framework-agnostic protocol helpers extracted from the rtl-433-hass Home
Assistant integration (Apache-2.0). Nothing here imports Home Assistant.
"""

from __future__ import annotations

from .client import CannotConnect, Rtl433Client
from .normalizer import NormalizedEvent, device_key, normalize
from .replay import ReplayVerdict, classify_replay

__all__ = [
    "CannotConnect",
    "NormalizedEvent",
    "ReplayVerdict",
    "Rtl433Client",
    "classify_replay",
    "device_key",
    "normalize",
]
