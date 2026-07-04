"""URL builders and getter-response unwrapping for the rtl_433 client.

Extracted from custom_components/rtl_433/coordinator/base.py of
rtl-433-hass/rtl_433 (Apache-2.0): the module-level ``_build_ws_url`` /
``_build_cmd_url`` helpers and the ``_unwrap_result`` staticmethod.
"""

from __future__ import annotations

from typing import Any


def build_ws_url(host: str, port: int, path: str, *, secure: bool = False) -> str:
    """Build a ``ws(s)://host:port/path`` URL from connection parameters."""
    scheme = "wss" if secure else "ws"
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{scheme}://{host}:{port}{path}"


def build_cmd_url(host: str, port: int, *, secure: bool = False) -> str:
    """Build the ``http(s)://host:port/cmd`` URL (server root, never the WS path).

    The ``/cmd`` endpoint always lives at the server root regardless of the
    configured WebSocket ``path``; graceful degradation behind a proxy that hides
    ``/cmd`` depends on this never being derived from the WebSocket path.
    ``secure`` maps ``wss`` => ``https`` and ``ws`` => ``http``.
    """
    scheme = "https" if secure else "http"
    return f"{scheme}://{host}:{port}/cmd"


def unwrap_result(payload: Any) -> Any:
    """Unwrap a ``{"result": <value>}`` getter response to its raw value.

    The HTTP ``/cmd`` responder (``rpc_response_jsoncmd``) wraps **every** getter
    reply in a ``result`` envelope -- not just the scalar getters
    (``get_gain``/``get_ppm_error``) but the JSON-payload getters
    (``get_meta``/``get_stats``) too. (Only the WebSocket framing sends those two
    as a bare object; this client uses ``/cmd``.) A bare value is accepted too, so
    this is safe across transports -- read defensively.
    """
    if isinstance(payload, dict) and "result" in payload:
        return payload["result"]
    return payload
