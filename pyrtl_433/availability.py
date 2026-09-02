"""Event-driven device classification for availability policy.

RF devices signal presence only by transmitting, so a consumer that marks a
device unavailable after N seconds of silence needs to know *which* devices have
a periodic check-in at all. Devices whose only transmissions are state changes —
a door opening, motion, a button press — have none, so any finite silence
timeout eventually misfires and wrongly hides a healthy device.

This module is the pure classifier half of that policy:

* :func:`known_field_keys` builds the restart-safe union of a device's field
  keys (what it reported before the consumer restarted, plus its latest live
  payload), so a device silent since startup is still classified from what it
  reported previously.
* :func:`is_event_driven` intersects those keys with the event-driven key set
  from the device library (:func:`pyrtl_433.library.event_driven_field_keys`).

The *timeout policy* itself is deliberately not here: the consumer maps the
boolean onto its own defaults (typically "never expire" for event-driven devices
and a finite periodic default for everything else).
"""

from __future__ import annotations

from collections.abc import Collection, Iterable


def is_event_driven(
    field_keys: Iterable[str], event_driven_keys: Collection[str]
) -> bool:
    """Whether a device's field keys mark it as event-driven (no check-in).

    ``True`` when any of ``field_keys`` appears in ``event_driven_keys``
    (open/close/motion/button/doorbell — transmits only on a state change, so
    availability never expires and conveys no freshness). An empty
    ``event_driven_keys`` (an empty or failed library load) yields ``False``, so
    a missing library degrades to the periodic default rather than pinning every
    device to never-expire.

    Args:
        field_keys: The device's known measurement field keys, typically from
            :func:`known_field_keys`.
        event_driven_keys: The event-driven key set for the active device
            library, from :func:`pyrtl_433.library.event_driven_field_keys`.

    Returns:
        ``True`` if the device is event-driven, else ``False``.
    """
    if not event_driven_keys:
        return False
    return not frozenset(event_driven_keys).isdisjoint(field_keys)


def known_field_keys(
    adopted: Iterable[str] | None, live: Iterable[str] | None
) -> set[str]:
    """Restart-safe union of a device's measurement field keys.

    Unions the persisted *adopted* fields (whatever the consumer stored for the
    device — these survive a restart) with the latest *live* payload's fields
    (the rtl_433 payload with identity and skip keys removed, i.e.
    ``NormalizedEvent.fields``). Reading both — not only the live payload — is
    what keeps a device that has been silent since a restart classified from what
    it reported before; classifying from the live payload alone leaves an event
    device on the periodic default and expires its battery and other sensors.

    Either side may be ``None`` (nothing persisted, or nothing seen this
    session).

    Args:
        adopted: Persisted field keys for the device, or ``None``.
        live: Field keys of the device's latest live payload, or ``None``.

    Returns:
        A new ``set`` with the union of both sides.
    """
    keys: set[str] = set()
    if adopted is not None:
        keys.update(adopted)
    if live is not None:
        keys.update(live)
    return keys
