# Device Library

`pyrtl_433.library` turns the JSON fields emitted by rtl_433 into **entity
descriptors** using a data-driven device library: a set of YAML files shipped
inside the package that map each rtl_433 field name to a descriptor.

The library is a loader + query layer, not an entity model. It answers three
questions and stops there:

- Should this field produce an entity at all? (`should_skip`)
- What kind of entity, with what unit / device class / unique-id suffix?
  (`lookup` → `FieldDescriptor`)
- What is the field's raw value, converted to the state you store?
  (`apply_transform`)

Its descriptor vocabulary (`platform`, `device_class`, `state_class`,
`entity_category`, `object_suffix`) is Home Assistant's MQTT-discovery
vocabulary, so a Home Assistant consumer maps it 1:1 — but nothing here imports
Home Assistant, and any consumer can read the same fields.

> The shipped library is a faithful port of the curated `mappings` table and
> `SKIP_KEYS` from rtl_433's own
> [`examples/rtl_433_mqtt_hass.py`](https://github.com/merbanan/rtl_433/blob/90621a8cb56c79b766077cadce4dc37bc613c54c/examples/rtl_433_mqtt_hass.py).
> The mapping *semantics* (device class, unit, state class, value transform,
> unique-id suffix) are reused; the MQTT transport is discarded.

## Consuming it from Python

```python
from pyrtl_433 import normalize
from pyrtl_433.library import apply_transform, load_library, lookup

registry, skip_keys = load_library()  # blocking file I/O — see below

event = {"model": "Acurite-Tower", "id": 1234, "temperature_C": 21.37}
normalized = normalize(event, skip_keys)  # identity + skip keys removed

for field_key, raw_value in normalized.fields.items():
    descriptor = lookup(field_key, normalized.model, registry=registry)
    if descriptor is None:
        continue  # unmapped field: build no entity
    state = apply_transform(descriptor, raw_value)
    print(descriptor.platform, descriptor.object_suffix, state)
    # sensor T 21.4
```

`load_library()` reads files, so an async consumer must run it off the event
loop:

```python
registry, skip_keys = await asyncio.to_thread(load_library)
```

`lookup` / `should_skip` / `event_driven_field_keys` all default to a cached,
lazily loaded copy of the shipped library when you pass no `registry` —
convenient for scripts, but a consumer that layers user overrides on top should
load once and pass its merged `registry` / `skip_keys` explicitly.

### Public surface

| Name | Purpose |
| --- | --- |
| `load_library(library_dir=None)` | Parse the YAML into `(Registry, skip_keys)`. Defaults to the packaged `library/data/` directory. Blocking I/O. |
| `Registry` | `flat: {field_key: FieldDescriptor}` plus `models: {model: {field_key: FieldDescriptor}}`. |
| `FieldDescriptor` | Frozen dataclass of one field's entity descriptor (the [attributes](#attributes) below). |
| `lookup(field_key, model=None, registry=None)` | Resolve a descriptor, model-scoped first (see [resolution order](#lookup-resolution-order)). |
| `should_skip(field_key, skip_keys=None)` | Whether a field is on the never-an-entity list. |
| `apply_transform(descriptor, raw_value)` | Raw rtl_433 value → the state to store. |
| `event_driven_field_keys(registry=None)` | The field keys that mark a device as event-driven (see [availability](#availability-classification)). |
| `merge_overrides(registry, skip_keys, override_data)` | Layer a parsed user override on top of a base library. Pure. |
| `validate_user_mappings(data)` | Validate a user-supplied override object → a list of problem strings (empty = valid). Pure, never raises. |
| `normalize_overrides(data)` | Deep-copied, JSON-serialisable, payload-canonical copy of an override object, for storage. Pure. |
| `USER_OVERRIDE_FILENAME` | Conventional filename (`rtl_433_mappings.yaml`) for a per-installation override file. The library never reads it itself. |
| `SKIP_KEYS_FIELD` / `MODELS_FIELD` | The two reserved top-level YAML keys (`skip_keys`, `models`). |

Every entry point is **defensive**: a malformed file, model, entry, or transform
parameter is logged (on the `pyrtl_433.library` logger) and skipped rather than
raising, so one bad mapping never prevents the rest of the library from loading.

## Mapping entry schema

Each library file is a YAML mapping whose **top-level keys are rtl_433 field
names** exactly as they appear in the JSON event (e.g. `temperature_C`,
`wind_avg_km_h`, `battery_ok`). Each value is an entry with the attributes
below.

Field names are matched **case-sensitively**, and not every decoder uses
`snake_case`. SCMplus, for instance, emits CamelCased fields — `Consumption`,
`MeterType`, `EndpointID` — so those keys are CamelCased in the library too. Copy
the name verbatim from the event or from the decoder source (SCMplus:
[`scmplus.c`](https://github.com/merbanan/rtl_433/blob/90621a8cb56c79b766077cadce4dc37bc613c54c/src/devices/scmplus.c#L126));
a key that differs only in case silently never matches.

```yaml
temperature_C:
  platform: sensor
  device_class: temperature
  unit_of_measurement: "°C"
  state_class: measurement
  name: null                  # let the consumer name it from device_class
  value_transform: { round: 1 }
  object_suffix: T
```

### Attributes

<table>
  <thead>
    <tr>
      <th style="min-width: 12rem;">Attribute</th>
      <th>Required</th>
      <th>Type</th>
      <th>Meaning</th>
    </tr>
  </thead>
  <tbody>
    <tr><td><code>platform</code></td><td>yes</td><td><code>sensor</code> | <code>binary_sensor</code> | <code>event</code></td><td>Which kind of entity the consumer creates. See <a href="#event-entities">Event entities</a> for <code>event</code>.</td></tr>
    <tr><td><code>device_class</code></td><td>yes (nullable)</td><td>string | <code>null</code></td><td>Device class (e.g. <code>temperature</code>, <code>humidity</code>, <code>safety</code>). Use <code>null</code> when the field has no appropriate device class. For <code>event</code> entries it is an event device class (<code>button</code>, <code>doorbell</code>).</td></tr>
    <tr><td><code>unit_of_measurement</code></td><td>yes (nullable)</td><td>string | <code>null</code></td><td>Unit of the transformed value. <code>null</code> for unitless or binary fields.</td></tr>
    <tr><td><code>state_class</code></td><td>yes (nullable)</td><td><code>measurement</code> | <code>total</code> | <code>total_increasing</code> | <code>null</code></td><td>Long-term-statistics class. <code>null</code> for binary fields and non-numeric sensors.</td></tr>
    <tr><td><code>name</code></td><td>no</td><td>string | <code>null</code></td><td>Human-readable entity name. <strong>Omit it (or set <code>null</code>) to let the consumer derive a name from <code>device_class</code></strong>. Only set an explicit name when it adds information the device class doesn't.</td></tr>
    <tr><td><code>object_suffix</code></td><td>yes</td><td>string</td><td>Short, stable token appended to the device key to form the entity's unique id. <strong>Must be stable</strong> — changing it orphans existing entities.</td></tr>
    <tr><td><code>value_transform</code></td><td>no</td><td>mapping</td><td>Declarative numeric transform applied before the value is stored. See <a href="#value-transforms">Value transforms</a>. Omit for binary fields.</td></tr>
    <tr><td><code>payload</code></td><td>no</td><td><code>{ on: &lt;raw&gt;, off: &lt;raw&gt; }</code></td><td>For <code>binary_sensor</code> only: maps the raw rtl_433 value to the on/off state. See <a href="#binary-payloads">Binary payloads</a>.</td></tr>
    <tr><td><code>event_map</code></td><td>no</td><td><code>{ &lt;raw-string&gt;: &lt;event-type&gt; }</code></td><td>For <code>event</code> only: maps a stringified raw value to a named event type. See <a href="#event-entities">Event entities</a>.</td></tr>
    <tr><td><code>clear_delay</code></td><td>no</td><td>int (seconds)</td><td>For <code>binary_sensor</code> only: seconds after a detection to <strong>synthesize</strong> an off, for detect-only hardware that sends no off. See <a href="#motion-occupancy">Motion / occupancy</a>.</td></tr>
    <tr><td><code>force_update</code></td><td>no</td><td>bool</td><td>Mirrors upstream <code>force_update</code>; write state even when the value is unchanged. Defaults to false.</td></tr>
    <tr><td><code>entity_category</code></td><td>no</td><td><code>diagnostic</code> | <code>config</code> | <code>null</code></td><td>Categorizes the entity. Diagnostic fields (battery, signal, tamper) use <code>diagnostic</code>.</td></tr>
    <tr><td><code>enabled_by_default</code></td><td>no</td><td>bool</td><td>Set <code>false</code> to register the entity disabled. Defaults to true.</td></tr>
    <tr><td><code>icon</code></td><td>no</td><td>string</td><td>Optional <code>mdi:</code> icon override.</td></tr>
    <tr><td><code>event_driven</code></td><td>no</td><td>bool</td><td>Marks a <code>binary_sensor</code> field as a state field that transmits <strong>only on a change</strong>, so the device has no periodic check-in. Defaults to false. See <a href="#availability-classification">Availability classification</a>. <code>platform: event</code> fields are always event-driven and do not need this flag.</td></tr>
  </tbody>
</table>

`null` is written explicitly (YAML `null`) rather than omitted for the three
"required (nullable)" attributes, so every entry is uniform and the loader never
has to guess intent. Optional attributes may simply be omitted.

**Unknown attributes are ignored** (with a debug log) rather than rejected, so a
newer library file that adds keys still loads on an older `pyrtl_433`. An entry
that is not a mapping, or a `clear_delay` / `event_driven` / `event_map` whose
type is wrong, is logged and dropped — the rest of the entry still loads.

### Value transforms

`value_transform` declares how to convert the raw JSON value into the stored
state, in place of a template string. Supported keys:

| Key      | Effect |
|----------|--------|
| `float`  | Coerce to float. |
| `int`    | Coerce to int. |
| `scale`  | Multiply by the given number. |
| `offset` | Add the given number (after `scale`). |
| `round`  | Round to N decimal places (the value of the key). |

Keys combine. The application order is: coerce (`float`/`int`) → `scale` →
`offset` → `round`. Examples drawn from the shipped library:

| rtl_433 field   | `value_transform`                    | Meaning |
|-----------------|--------------------------------------|---------|
| `temperature_C` | `{ round: 1 }`                       | one decimal place |
| `humidity`      | `{ float: true }`                    | plain float |
| `lux`           | `{ int: true }`                      | plain int |
| `wind_avg_m_s`  | `{ scale: 3.6, round: 2 }`           | m/s → km/h |
| `battery_ok`    | `{ scale: 99, offset: 1, round: 0 }` | 0/1 → 1 %/100 % |

`scale`, `offset` and `round` all imply float coercion, so `{ round: 1 }` is
equivalent to `{ float: true, round: 1 }` and the shorter form is preferred;
`int` applies only when no float-implying key is present. `round` to 0 or fewer
digits normalizes a whole number back to `int`, so `battery_ok` reads as `100`
rather than `100.0`.

A **non-numeric raw value passes through unchanged** (`apply_transform` returns
it as-is), as does a `None`. An invalid `scale` / `offset` / `round` parameter is
logged and that step is skipped; the rest of the pipeline still runs.

### Binary payloads

`binary_sensor` entries use `payload` instead of `value_transform`. It maps the
raw rtl_433 value to the on/off state:

```yaml
detect_wet:
  platform: binary_sensor
  device_class: moisture
  payload: { on: "1", off: "0" }   # 1 == wet == on
```

`apply_transform` returns `True` for the `on` token, `False` for the `off`
token, and **`None` when the value matches neither** — so the consumer decides
how to render an unknown state. Matching is string-based (rtl_433 emits values
as strings and numbers), with a numeric near-equality fallback so a raw `1.0`
still matches the token `"1"`. A `binary_sensor` with no `payload` at all falls
back to a truthy reading of `1` / `true` / `on` / `yes`.

Note the direction matters for some fields. The upstream `closed` field is
**inverted** — a value of `0` means the contact is open — so its payload is
`{ on: "0", off: "1" }`.

!!! note "YAML 1.1 parses bare `on` / `off` as booleans"

    PyYAML reads `{ on: "1", off: "0" }` as `{True: "1", False: "0"}`. The
    loader canonicalizes those (and `normalize_overrides` does the same for user
    overrides) back to the string keys `"on"` / `"off"`, so a descriptor's
    `payload` always has string keys and is safe to JSON-serialise.

> **`battery_ok` note.** rtl_433's `battery_ok` is boolean-ish (`1` = OK,
> `0` = low). The upstream example does *not* model it as a binary sensor; it
> converts it to a battery **percentage** sensor (`0` → 1 %, `1` → 100 %). The
> shipped library preserves that: `battery_ok` is a `sensor` with
> `device_class: battery`, `unit: "%"`, and
> `value_transform: { scale: 99, offset: 1, round: 0 }`. If you prefer a
> low-battery binary problem sensor, override it (see
> [Override merge semantics](#override-merge-semantics)).

### Motion / occupancy

PIR / occupancy decoders (Interlogix, Risco Agility, Kerui, …) emit `motion`
**only on detection** (raw value `1`) and **never send an off** — the hardware is
detect-only. So `motion` is a `binary_sensor` (device class `occupancy`) whose
`payload` declares only an `on` token; the off state is **synthesized** by a
timer rather than received:

```yaml
motion:
  platform: binary_sensor
  device_class: occupancy
  name: Motion
  payload: { on: "1" }   # detect-only: no off token
  clear_delay: 90        # synthesize off 90 s after the last detection
  object_suffix: motion
```

`clear_delay` (seconds) is **data, not behaviour**: the library only parses and
validates it (a non-integer, a `bool`, or a value ≤ 0 is logged and dropped).
The consumer owns the timer — turn the sensor `on` on each detection and clear
it to off after `clear_delay` elapses with no re-detection, rescheduling on every
retrigger. The shipped default is 90 s.

### Availability classification

RF devices signal presence only by transmitting, so a consumer that marks a
device unavailable after N seconds of silence needs to know which devices have a
periodic check-in at all. A field is **event-driven** when it uses
`platform: event` **or** sets `event_driven: true` (e.g. `motion`,
`contact_open`, `reed_open`, `closed`, `alarm`): the device transmits *only on a
state change*, so any finite silence timeout eventually misfires and wrongly
hides a healthy device.

`event_driven_field_keys(registry)` returns that key set, derived from the active
registry — the global table *and* every model-scoped overlay — so it stays in
sync with the shipped library and any user overrides. Pair it with
[`pyrtl_433.availability`](api-reference.md#module-map):

```python
from pyrtl_433.availability import is_event_driven, known_field_keys
from pyrtl_433.library import event_driven_field_keys

event_keys = event_driven_field_keys(registry)

# `adopted` is whatever field keys you persisted for the device (they survive a
# restart); `live` is the latest payload's fields. Unioning both is what keeps a
# device that has been silent since a restart classified correctly.
fields = known_field_keys(adopted, live)
timeout = NEVER if is_event_driven(fields, event_keys) else PERIODIC_DEFAULT
```

The **timeout values are yours**: `pyrtl_433` ships the classifier, not the
policy. A typical mapping is never-expire for event-driven devices and a finite
default (10 minutes, say) for periodic ones. An empty `event_driven_keys` — a
failed or empty library load — classifies everything as periodic rather than
pinning every device to never-expire.

Diagnostic fields such as `battery_ok` do not decide the class on their own: if a
device *also* has an event-driven field, the whole device is event-driven, so its
battery and other entities stay available between events.

### Event entities

`platform: event` is for **momentary, fire-and-forget** RF fields — a remote
button, a doorbell press — that have no steady on/off state to track. Each
genuine transmission fires one event, and the entity stays available between
presses (no faked "off"). Event entries live in `events.yaml`:

```yaml
button:
  platform: event
  device_class: button
  name: Button
  object_suffix: button
```

How event entries differ from `sensor` / `binary_sensor`:

- **The fired event type is the stringified raw value** by default (`str(value)`).
  There is no `payload` and no `value_transform`.
- **Event types are discovered, not declared.** A consumer records each newly
  observed value as a valid type the first time it is seen; you never list them
  in the YAML.
- `device_class` is an event device class (`button`, `doorbell`).

#### `event_map`: naming raw values

The optional `event_map` attribute overrides the default stringified behaviour:
it maps a **stringified raw value → named event type**. Both keys and values are
coerced to `str` by the loader (rtl_433 emits values as strings and numbers), and
a non-mapping `event_map` is logged and dropped. When present:

- A transmission whose raw value is in the map fires the **mapped** type; values
  **not** in the map still pass through as `str(value)`.
- The mapped types can be **declared up front** by the consumer (in map order)
  rather than only appearing once observed.

The doorbell is the shipped example. `secret_knock` is emitted on **every**
press: raw `0` is a regular single press and raw `1` is a "secret knock" (the
button pressed three times rapidly):

```yaml
secret_knock:
  platform: event
  device_class: doorbell
  name: Doorbell
  object_suffix: secret_knock
  event_map:
    "0": ring          # the standard doorbell type
    "1": secret_knock  # custom type for the 3x-rapid "secret knock"
```

## Model-scoped mappings (`models:`)

The top-level keys above are the **global** defaults: a `temperature_C` entry
applies to *every* device that emits `temperature_C`. Some fields, though, need
a different descriptor depending on the **device model** — most notably the
utility-meter consumption counters (`Consumption`, `consumption_data`), whose
unit and scale are *not* carried in the RF signal and differ between meter
models. For those, a file may carry an optional top-level **`models:`** block
that overrides the global descriptor for one specific rtl_433 `model` string.

`models:` is keyed by the exact rtl_433 `model` value, and each model maps to a
table of `field_key → descriptor` using the **same per-field attribute schema**
as the global entries:

```yaml
# top-level global defaults (unchanged) live here ...
temperature_C:
  platform: sensor
  device_class: temperature
  unit_of_measurement: "°C"
  state_class: measurement
  name: Temperature
  value_transform: { round: 1 }
  object_suffix: T

# ... and an optional model-scoped block sits alongside them:
models:
  Some-Model-Name:            # an exact rtl_433 `model` string
    consumption_data:
      platform: sensor
      device_class: energy
      unit_of_measurement: kWh
      state_class: total_increasing
      name: Consumption
      value_transform: { scale: 1 }
      object_suffix: consumption
```

The `models:` block is **additive and optional**: every file without one parses
exactly as before, and the flat top-level keys remain the global default. It may
appear in any library file (most naturally `power_electrical.yaml`) and in a user
override. `models` is a reserved top-level key — the loader intercepts it, so you
cannot have a *field* literally named `models`.

### Lookup resolution order

`lookup(field_key, model, registry=...)` resolves **most-specific first**:

1. The **model-scoped** entry for `(model, field_key)`, if the device's model has
   a `models:` block with that field.
2. Otherwise the **global** flat entry for `field_key`.
3. Otherwise `None` — the field is unmapped, so build no entity.

So a `models:` entry only affects the model it names; every other model keeps the
global descriptor for that same field. Passing `model=None` resolves only the
global entry.

> **No speculative real-meter mappings ship.** Because a meter's consumption
> unit/scale is not knowable from the signal, the shipped library does **not**
> carry a guessed `models:` consumption mapping for any real model — a wrong
> scale would silently corrupt real energy data. The example above is purely
> illustrative.

## The skip-keys file

`_skip_keys.yaml` lists fields that must never produce an entity — device
identity (`model`, `id`, `channel`, `subtype`, `type`, and SCMplus's duplicate
`EndpointID`), message bookkeeping (`mic`, `mod`, `sequence_num`,
`message_type`, `exception`, `raw_msg`, SCMplus's `PacketCRC`), and the
secondary radio-tuning fields (`freq1`, `freq2`, `protocol`). The primary per-event `freq` is **not** skipped — it is
mapped in `misc.yaml` to a diagnostic Frequency sensor (disabled by default)
alongside `rssi`, `snr`, and `noise`:

```yaml
skip_keys:
  - type
  - model
  - id
  # ...
```

`load_library()` returns this list as the second element of its tuple. Check a
field against it (or hand it to
[`normalize`](api-reference.md#module-map), which drops those keys from
`NormalizedEvent.fields` for you) *before* attempting a `lookup`. Identity keys
(`model` + `id`/`channel`/`subtype`) are consumed by the event normalizer to
derive the device key, which is why they are skipped here rather than mapped.

!!! note "`normalizer.DEFAULT_SKIP_KEYS` is not this list"

    `pyrtl_433.normalizer.DEFAULT_SKIP_KEYS` is a frozen five-key fallback that
    keeps `normalize` usable with no loader at all. The real skip list is
    `_skip_keys.yaml`, loaded here; pass it to `normalize` explicitly.

A missing or malformed `_skip_keys.yaml` yields an empty set (and a warning), so
a bad skip file never blocks startup.

## Override merge semantics

`merge_overrides(registry, skip_keys, override_data)` layers a **parsed** user
override (the same schema as a library file) on top of a base library and
returns new `(registry, skip_keys)` objects — it is pure, does no I/O, and never
mutates its inputs, so one base library can be merged differently per
installation without cross-contamination.

- A flat field present in both: the **override wins**, as a *full entry
  replacement*, not a deep merge.
- A flat field present only in the override: it is **added**.
- `skip_keys` in the override is **unioned** with the base list.
- A `models:` block is merged **per `(model, field_key)`**: an override
  model-scoped entry replaces the base one for the same model and field, while
  other base model fields are preserved.
- A malformed individual override entry is logged and skipped; the rest of the
  override still applies. A non-mapping `override_data` is logged and ignored
  entirely.

Because `lookup` checks the model tier before the global tier, the full
precedence for one field on one device is **specificity-first**, highest to
lowest:

1. **Model-scoped** entry — override `models:` entry, else shipped `models:` entry.
2. **Global** flat entry — override flat key, else shipped flat key.
3. Unmapped → no entity.

In particular a **shipped** `models:` entry outranks an **override global**
entry for a matching model.

Two pure helpers support a configuration surface around this:

- `validate_user_mappings(data)` returns a list of self-contained problem
  strings (empty means valid), deliberately mirroring what the loader accepts so
  "the validator accepts it ⇒ the merge keeps it" holds. `None` is valid; a
  non-mapping top level is one problem; each entry must be a mapping with a
  non-empty `platform` and `object_suffix`, and any `platform` must be one of
  `sensor` / `binary_sensor` / `event`. Unknown extra attributes are tolerated.
- `normalize_overrides(data)` returns a deep-copied, JSON-serialisable copy with
  every `payload` canonicalized to string `on` / `off` keys — what you store.

```python
import yaml

from pyrtl_433.library import load_library, merge_overrides, validate_user_mappings

registry, skip_keys = load_library()
override = yaml.safe_load(user_text)

problems = validate_user_mappings(override)
if problems:
    raise ValueError("; ".join(problems))

registry, skip_keys = merge_overrides(registry, skip_keys, override)
```

## Where the files live

```
pyrtl_433/library/data/
├── _skip_keys.yaml         # fields that never become entities
├── air_quality.yaml        # pm2.5 / pm10 / co2
├── binary_states.yaml      # contacts, tamper, alarm, door state
├── events.yaml             # momentary RF: button, doorbell
├── humidity_moisture.yaml  # humidity, moisture, leak, depth, WH51 soil AD/boost
├── light_uv.yaml           # illuminance, UV
├── misc.yaml               # battery %, timestamp, signal, lightning
├── power_electrical.yaml   # power, energy, current, voltage, consumption
├── pressure.yaml           # barometric pressure
├── rain.yaml               # rain total / rate
├── temperature.yaml        # temperature variants
└── wind.yaml               # wind speed / gust / direction
```

`load_library()` reads **every** `*.yaml` file in this directory, merges all
entries into one lookup table keyed by field name, and reads `_skip_keys.yaml`
separately as the exclusion list. Grouping is purely organizational: put a new
field in whichever file fits its domain, or in `misc.yaml` if nothing fits.
Files whose name starts with `_` are not parsed as field-mapping tables. Later
files override earlier ones on a key collision (and warn), and a file that fails
to parse is logged and skipped rather than aborting the load.

Point `load_library(library_dir)` at your own directory to load a private
library instead of the packaged one; the schema is identical.

## Adding a mapping

1. **Find the field name.** Watch your rtl_433 stream and collect the field keys
   that `lookup` resolves to `None`. Each is either a candidate for a mapping or,
   if it is genuinely noise/identity data, an entry for `_skip_keys.yaml`.
   rtl_433 field names are case-sensitive and unit-suffixed (`temperature_C`,
   not `temperature`).
2. **Pick the file** that matches the field's domain, or `misc.yaml`.
3. **Add an entry** keyed by the exact field name, filling in the required
   attributes. Copy a similar existing entry as a template.
     - For a numeric reading: `platform: sensor`, the closest device class, the
       unit rtl_433 reports, a `state_class` (`measurement` for instantaneous
       readings, `total_increasing` for monotonic counters like rain or energy),
       and a `value_transform`.
     - For a boolean: `platform: binary_sensor`, a device class, and a `payload`
       mapping. Leave `unit_of_measurement` / `state_class` `null`.
     - Choose a short, **stable** `object_suffix`, unique among the fields a
       single device emits.
4. **Add a fixture** under `tests/fixtures/` with a real event from your device.
   `tests/test_fixture_coverage.py` sweeps every fixture and fails if any field
   in it has no descriptor and no skip-key entry, so a fixture is what proves
   your mapping actually matches.

!!! warning "Field names are matched exactly, and a mismatch is silent"

    A key that differs from the wire name by so much as its case produces no
    entity, no warning, and no error — the sensor simply never appears. SCMplus
    emits `Consumption` (CamelCase) while ERT-SCM emits `consumption_data`
    (snake_case); both decoders are in the same protocol family. Copy the name
    from the decoder's `data_make()` call, or better, from a real event.

    For the SCM family and Acurite this is checked against rtl_433's actual
    output: `tests/fixtures/generated/` holds events decoded from real `.cu8`
    captures. See `tests/fixtures/generated/README.md`.

## Fields that cannot be expressed declaratively

The upstream `mappings` table includes two `device_automation` entries —
`channel` and `button` — that publish MQTT **device triggers** rather than
entities. These have no `sensor` / `binary_sensor` equivalent in this schema:

- `channel` is already a device-identity key and lives in `_skip_keys.yaml`.
- `button` is modelled as an [event entity](#event-entities) instead — see
  `events.yaml`.

Everything else from the upstream table is ported faithfully.
