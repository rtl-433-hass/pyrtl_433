"""Device naming helpers for the rtl_433 client library.

Extracted from custom_components/rtl_433/normalizer.py and
custom_components/rtl_433/entity.py of rtl-433-hass/rtl_433 (Apache-2.0).

:func:`~pyrtl_433.normalizer.device_key` derives a deterministic device key
shaped ``<model-token>-<id>[-ch<channel>][-st<subtype>]`` (only the parts the
event actually carried; a model-only device is just ``<model-token>``). This
module turns that key back into presentation values:

- :func:`safe_token` — the token builder the key itself is made of.
- :func:`display_name` — ``"<model> <id-suffix>"``, the model plus only the part
  of the key that distinguishes one unit from another.
- :func:`identity_suffix` — that ``<id-suffix>`` on its own, for publishing as a
  serial number.

**Frozen contract.** :func:`safe_token`'s output is embedded in consumers'
stable identifiers (the Home Assistant integration builds unique_ids, entity ids
and dispatcher signals from it), so changing what it emits for any input would
orphan every entity already created from it. Treat its behaviour as frozen:
extend it only in ways that cannot change an existing input's token.

This module imports nothing else from the package, so it is safe to import from
anywhere in it.
"""

from __future__ import annotations

from typing import Any


def safe_token(value: Any) -> str:
    """Return an HA-safe token for an identity value.

    Keeps the token deterministic and human-readable: only characters that are
    unsafe in unique_ids / dispatcher signals (whitespace and ``/``) are
    collapsed to underscores. The same input always produces the same token.

    Args:
        value: Any identity value (``str``, ``int``, ...); stringified first.

    Returns:
        The token: the stringified value with alphanumerics and ``-``, ``_``,
        ``.`` preserved, every other character replaced by ``_``, edge
        underscores stripped, and ``"unknown"`` when nothing is left.

    Examples:
        ``"Acurite-606TX"``   -> ``Acurite-606TX``
        ``"Brand X/Model 1"`` -> ``Brand_X_Model_1``
        ``"/ /"``             -> ``unknown``

    Note:
        The output is part of a frozen compatibility contract (see the module
        docstring): consumers embed it in stable identifiers, so it must never
        change for an input that already produces a token.
    """
    text = str(value).strip()
    out: list[str] = []
    for ch in text:
        if ch.isalnum() or ch in ("-", "_", "."):
            out.append(ch)
        else:
            out.append("_")
    token = "".join(out).strip("_")
    return token or "unknown"


def display_name(model: str, device_key: str) -> str:
    """Human-readable device name: the model plus its distinguishing id suffix.

    ``device_key`` is ``<model-token>-<id>[-ch..][-st..]`` (see
    :func:`~pyrtl_433.normalizer.device_key`), so naively combining the model
    with the whole key duplicates the model — e.g.
    ``Fineoffset-WH51 (Fineoffset-WH51-00c50f)``. Strip the model-token prefix
    and keep only the suffix, giving ``Fineoffset-WH51 00c50f``: the canonical
    rtl_433 model (matching the device's ``model`` field) plus just the id that
    distinguishes one unit from another.

    Args:
        model: The event's ``model`` string (empty when it never decoded).
        device_key: The device key derived from the same event.

    Returns:
        ``"<model> <id-suffix>"``, the raw ``device_key`` when there is no
        model, or the bare ``model`` for a model-only device (no suffix) and for
        a key that is not shaped as ``<model-token>-<suffix>``.

    Examples:
        ``("Fineoffset-WH51", "Fineoffset-WH51-00c50f")`` -> ``Fineoffset-WH51 00c50f``
        ``("Foo", "Foo")``                                -> ``Foo``
        ``("", "UnknownDevice-7")``                       -> ``UnknownDevice-7``
    """
    if not model:
        return device_key
    suffix = identity_suffix(model, device_key)
    if suffix is None:
        # Model-only device (key == model token), or an unexpected key shape.
        return model
    return f"{model} {suffix}"


def identity_suffix(model: str, device_key: str) -> str | None:
    """Return the identity suffix of ``device_key``: the decoded id and channel.

    This is the same ``<id>[-ch..][-st..]`` suffix that :func:`display_name`
    folds into the device name, surfaced separately so it can be published as a
    serial number that survives a user rename — the name is the user's to
    change, the serial number stays the transmitter's. The channel/subtype parts
    are transmitter identity too, so they stay in the suffix.

    Args:
        model: The event's ``model`` string (empty when it never decoded).
        device_key: The device key derived from the same event.

    Returns:
        The suffix, or ``None`` when there isn't one, so the caller can leave
        the field unset rather than publish a misleading serial: for a
        model-only device (the key is just the model token, so nothing
        distinguishes one unit from another), when the model is unknown, and
        when the key is not shaped as ``<model-token>-<suffix>``.

    Examples:
        ``("Acurite-986", "Acurite-986-1a2b-ch2")``   -> ``1a2b-ch2``
        ``("Fineoffset-WH51", "Fineoffset-WH51")``    -> ``None``
        ``("Acurite-986", "Nexus-TH-77")``            -> ``None``
    """
    if not model:
        return None
    suffix = device_key.removeprefix(f"{safe_token(model)}-")
    if not suffix or suffix == device_key:
        # Model-only device (key == model token), or an unexpected key shape.
        return None
    return suffix
