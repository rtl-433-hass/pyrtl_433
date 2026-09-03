# Changelog

## [0.4.0](https://github.com/rtl-433-hass/pyrtl_433/compare/v0.3.0...v0.4.0) (2026-09-03)


### Features

* **replay:** report the server's event time resolution ([#40](https://github.com/rtl-433-hass/pyrtl_433/issues/40)) ([dfc9390](https://github.com/rtl-433-hass/pyrtl_433/commit/dfc93907d8f0e725fa027b684e3abdad17f6722e))


### Bug Fixes

* **replay:** parse epoch-seconds timestamps (report_meta time:unix) ([#39](https://github.com/rtl-433-hass/pyrtl_433/issues/39)) ([fedb277](https://github.com/rtl-433-hass/pyrtl_433/commit/fedb27783fe3bb84222f82532aa998d0aa8997bb))
* **replay:** separate a repeated transmission from a new one sharing its stamp ([#41](https://github.com/rtl-433-hass/pyrtl_433/issues/41)) ([ff20bd9](https://github.com/rtl-433-hass/pyrtl_433/commit/ff20bd92786a75f9930339c8014181fb50aa8eae))

## [0.3.0](https://github.com/rtl-433-hass/pyrtl_433/compare/v0.2.0...v0.3.0) (2026-09-02)


### Features

* **library:** ship the rtl_433 device-mapping library ([#33](https://github.com/rtl-433-hass/pyrtl_433/issues/33)) ([7cba591](https://github.com/rtl-433-hass/pyrtl_433/commit/7cba591a966bae319393fe9cb04b689e467c1f3f))
* **naming:** publish device-naming helpers ([#35](https://github.com/rtl-433-hass/pyrtl_433/issues/35)) ([54a0cbd](https://github.com/rtl-433-hass/pyrtl_433/commit/54a0cbdcf75b617cdc90ef4ee238220eb04a78f3))


### Bug Fixes

* track replay high-water mark per device ([#32](https://github.com/rtl-433-hass/pyrtl_433/issues/32)) ([f1952e0](https://github.com/rtl-433-hass/pyrtl_433/commit/f1952e082a2cb5732c659b38add49e88614db9aa))


### Documentation

* drop the stale "not yet published to PyPI" wording ([65a5ae6](https://github.com/rtl-433-hass/pyrtl_433/commit/65a5ae6090bf611b3697f371ab394578142b9249))

## [0.2.0](https://github.com/rtl-433-hass/pyrtl_433/compare/v0.1.1...v0.2.0) (2026-07-17)


### Features

* **client:** parse "Auto Level" log frames into noise/min-level state ([#9](https://github.com/rtl-433-hass/pyrtl_433/issues/9)) ([c7559e6](https://github.com/rtl-433-hass/pyrtl_433/commit/c7559e6ed338357c3120cbe82186bcb6b8bca086))

## [0.1.1](https://github.com/rtl-433-hass/pyrtl_433/compare/v0.1.0...v0.1.1) (2026-07-05)


### Bug Fixes

* **replay:** interpret naive timestamps in an injectable time zone ([aa71400](https://github.com/rtl-433-hass/pyrtl_433/commit/aa71400bbce388f2e48e21397e7febad2712a58d))

## 0.1.0 (2026-07-04)


### Features

* add decoupled Rtl433Client transport ([acf80ae](https://github.com/rtl-433-hass/pyrtl_433/commit/acf80aed7597ff37d96cd362388ce00132cfc077))
* migrate pure protocol helpers and mutation-testing kit ([d5f5e5c](https://github.com/rtl-433-hass/pyrtl_433/commit/d5f5e5cb75e9efc2ee1dfaee119c028f98149d64))


### Documentation

* add README with quickstart, protocol reference, and test contract ([a70bbfa](https://github.com/rtl-433-hass/pyrtl_433/commit/a70bbfa64c6f2865a787ffe615fddd9cbf0cd43f))
* drop uv-first / not-yet-on-PyPI note ([d914b9e](https://github.com/rtl-433-hass/pyrtl_433/commit/d914b9e2206faf64d5359a2b28a7140b7362a09f))
* merge Installation into Getting Started page ([aad8e4f](https://github.com/rtl-433-hass/pyrtl_433/commit/aad8e4f9ad8ae818cdc895b97487435de53f241a))
* publish a versioned MkDocs site and trim the README ([d4c1bd4](https://github.com/rtl-433-hass/pyrtl_433/commit/d4c1bd48eabbfcdca4b90911050dce07957c4c87))
* remove deliberate non-scope section ([bdb66b3](https://github.com/rtl-433-hass/pyrtl_433/commit/bdb66b3ec00c558c6fe2d7900060bdb5759eeb08))
* remove obvious sentence ([c5e2faf](https://github.com/rtl-433-hass/pyrtl_433/commit/c5e2faf6b3c3c3f1fe89188de981dfa90b20c3f5))
* remove per-module mutation-score table ([1c4526f](https://github.com/rtl-433-hass/pyrtl_433/commit/1c4526f11301d274b85e9c37675996855523ebbc))
