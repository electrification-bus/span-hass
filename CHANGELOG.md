# Changelog

All notable changes to `span-hass` are recorded here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

The 0.1.0 line is the initial alpha. All work to date is grouped here pending the first tagged release; once `v0.1.0` is tagged, this section will move under `[0.1.0]` with the tag date.

### Added

- Initial release of the SPAN Panel (eBus) Home Assistant custom integration. Local-push MQTT integration over the panel's built-in TLS broker, with no cloud dependency and no polling. Entities are auto-generated from the panel's Homie `$description`.
- **mDNS discovery** via `_ebus._tcp` and `_secure-mqtt._tcp`. Panels appear automatically in `Settings > Devices & Services` once the integration is installed and HA has been restarted.
- **Config flow** with two authentication paths: HOP passphrase or door-bypass (open panel door + press door switch 3× rapidly). Panel serial and firmware are shown during auth so the user can confirm they're talking to the right panel.
- **Multi-panel support.** Each physical panel is a separate config entry. A `link_subpanel` service sets `via_device` in the device registry so daisy-chained panels nest correctly under their parent for Sankey hierarchy.
- **Circuit entities** — Power (W), Energy (Wh consumed), Energy Returned (Wh backfed), Current (A), Relay switch, Shed Priority select, Breaker Rating, Space. Circuit names come from the panel's user-assigned labels; setup waits for these before creating entities so unique-IDs are stable.
- **Generation Power entity** for circuits that feed PV into the panel — surfaces the negative-going share of `active-power` as a positive `power` sensor with `device_class=power` so the HA Energy Dashboard can plot circuit-level solar production.
- **Panel system entities** — Door (binary, tamper class), Ethernet / Wi-Fi / Cellular connectivity (binary), Firmware Version, L1 / L2 / L3 Voltage, Main Breaker Rating, Dominant Power Source select.
- **Upstream-lug entities** — grid Energy (consumed), Energy Returned (exported), Power.
- **Battery storage (BESS) entities** — State of Charge, State of Energy, Connected, Nameplate Capacity, Relative Position (UPSTREAM / IN_PANEL), Vendor / Model / Serial, Feed Circuit.
- **Solar PV entities** — Nameplate Capacity, Relative Position, Vendor / Product, Feed Circuit.
- **EV charger entities** — Status, Lock State, Advertised Current, Feed Circuit.
- **Energy Dashboard ready.** Entities carry the correct `device_class` / `state_class` / unit metadata so the HA Energy Dashboard "Now" power Sankey and the energy bar chart work without manual template overrides.
- **HA brand images.** `custom_components/span_ebus/brand/` ships SPAN icon and logo PNGs (1× and 2×) for HA 2026.3+'s custom-integration brand-image support — the integration shows up with a proper logo instead of the default placeholder.
- **Memory diagnostics.** Every 30 minutes the integration logs peak RSS, tracemalloc top-5 allocators, and paho-mqtt queue depths. Near-zero overhead and proven invaluable for diagnosing memory issues on resource-constrained hardware (HA Yellow, etc.).
- Documentation covering setup, entity model, energy-direction conventions, known SPAN firmware bugs, and Energy Dashboard configuration — see [`README.md`](README.md) and [`docs/`](docs/).

### Fixed

- **Memory leak on multi-panel installs.** Each `Controller` now passes `device_id` so it subscribes only to its own panel's MQTT topics. Without this, three controllers on three panels each discovered all three panels (9× message processing) and accumulated retained-state for the other two. Combined with explicit `stop()` cleanup on `Controller` and `MqttClient`, this resolves the HA Yellow freeze observed after ~4–8 hours of runtime.
- **Energy counter monotonicity.** `total_increasing` energy sensors now suppress occasional decreases reported by the panel firmware. Decreases would otherwise cause the HA statistics engine to treat the value as a counter reset and lose energy history. Workaround pending an upstream SPAN firmware fix.
- **Entity class attributes persist across restarts.** Entity class attributes are now set unconditionally at construction time rather than via lazy `_attr_*` paths, so the HA registry retains the correct device-class / state-class assignments across restarts even when the panel hasn't yet republished `$description`.

### Changed

- **MQTT QoS 1 default.** The integration subscribes at QoS 1 (at-least-once) rather than QoS 2. For continuously-updating sensor data, QoS 1 is adequate — a duplicate or missed power reading is harmless. QoS 2's four-step handshake adds complexity, and paho-mqtt holds in-flight QoS 2 messages with no timeout, creating an accumulation vector if any step is lost. Configurable via `EBUS_HOMIE_MQTT_QOS` (supported by the SDK) or by editing `MQTT_QOS` in `const.py`.
- **Generic example serial numbers** in documentation. Real panel serials were replaced with generic `nt-2024-a1b2c` / `nt-2024-d3e4f` examples to avoid leaking info about specific deployed panels.

### Known issues

- SPAN firmware declares `unit="kW"` for circuit `active-power` and PV `nameplate-capacity` but the actual values are in watts. The integration overrides the declared unit; other properties (upstream lugs, power-flows) declare correct units.
- The SPAN import/export energy direction convention (circuit `exported-energy` = consumption, upstream `imported-energy` = grid consumption) is not documented in the SPAN API and was reverse-engineered. See `README.md` §"Energy Flows and Import/Export" and the energy-counter monotonicity docs in [`docs/`](docs/).
- After installing the integration for the first time, HA may need to be restarted **twice** before mDNS discovery picks up panels — a known limitation of how HA loads zeroconf service types for custom integrations on first install.

[Unreleased]: https://github.com/electrification-bus/span-hass/commits/main
