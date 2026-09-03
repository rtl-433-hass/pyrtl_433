"""pyrtl_433 -- a standalone async client for the rtl_433 WebSocket/HTTP API.

The framework-agnostic protocol helpers extracted from the rtl-433-hass Home
Assistant integration (Apache-2.0). Nothing here imports Home Assistant.

The most commonly used names from the :mod:`pyrtl_433.library` device-mapping
subpackage are re-exported here; the rest of its surface (``merge_overrides``,
``validate_user_mappings``, ``event_driven_field_keys``, ...) is imported from
that subpackage directly.
"""

from __future__ import annotations

from .autolevel import AUTO_LEVEL_SRC, AutoLevelReading, parse_auto_level
from .availability import is_event_driven, known_field_keys
from .client import CannotConnect, Rtl433Client
from .library import FieldDescriptor, Registry, apply_transform, load_library, lookup
from .naming import display_name, identity_suffix, safe_token
from .normalizer import NormalizedEvent, device_key, normalize
from .replay import (
    PayloadIdentity,
    ReplayVerdict,
    TimePrecision,
    classify_replay,
    payload_identity,
    time_precision,
)

__all__ = [
    "AUTO_LEVEL_SRC",
    "AutoLevelReading",
    "CannotConnect",
    "FieldDescriptor",
    "NormalizedEvent",
    "PayloadIdentity",
    "Registry",
    "ReplayVerdict",
    "Rtl433Client",
    "TimePrecision",
    "apply_transform",
    "classify_replay",
    "device_key",
    "display_name",
    "identity_suffix",
    "is_event_driven",
    "known_field_keys",
    "load_library",
    "lookup",
    "normalize",
    "parse_auto_level",
    "payload_identity",
    "time_precision",
    "safe_token",
]
