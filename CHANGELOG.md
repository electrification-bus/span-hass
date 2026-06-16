# Changelog

All notable changes to `span-hass` are recorded here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] — TBD

The 0.2.0 release migrates the integration to the **G3P-23496 parent/child Homie 5 data model** that lands in SPAN firmware r202627. The panel publishes itself as a tree — panel root + per-lug / per-BESS / per-MID / per-PV / per-EVSE / per-circuit child devices — and the integration walks that tree, registering one HA device per Homie device. The old flat data model is gone.

> **BREAKING — unique-IDs reset.** Every entity gets a new `unique_id` of the form `{panel-serial}_{device-id}_{capability}_{property-id}` reflecting the new tree shape. No 0.1.x → 0.2.0 translation is provided; old entities surface as unavailable orphans in HA's registry after upgrade. Recommended cleanup is **delete + re-add each panel config entry** for a clean state. See README §"Upgrading from 0.1.x" for details.

### Added

- **Tree-walked entity model.** Each Homie device becomes its own HA device: panel root + 2 lugs devices (upstream / downstream) + 1 BESS per commissioned battery + 1 MID grandchild per BESS + 1 PV per inverter + 1 EVSE + 1 device per circuit. Per-device entities are scoped to their owning device rather than all hanging off the panel.
- **MID (Microgrid Interconnect Device) entities** — every commissioned BESS now has a synthesized MID grandchild with vendor / serial / product / model / firmware-version / hardware-version diagnostics plus `islanding-state` (ON_GRID / OFF_GRID / UNKNOWN), `grid-state` (UP / DOWN / DEGRADED / UNKNOWN), and `grid-forming-entity` ("GRID" when grid-tied, the BESS device-id when islanded). Surfaces what's actively forming the grid at any moment, particularly useful during outages.
- **Shed forecast (BTR — battery time remaining) entities** — `total-time-remaining`, `time-to-priority-shed`, plus full-charge variants of both, all in minutes, plus a `confidence` (LOW/MEDIUM/HIGH) enum. Surfaces SPAN's runtime estimate of how long the panel can sustain its current load on battery.
- **Shed override switch** — a settable switch on the panel root that forces shed-priority configuration to actually shed. Replaces the settable half of the retired 0.1.x `core/dominant-power-source` select. The panel firmware silently ignores writes outside the spec-defined preconditions (off-grid + degraded BESS comms).
- **Connection capability** on lugs and circuits — publishes `feeds-device-id` / `feeds-device-type` / `feeds-device-status` (and `fed-by-*` on upstream lugs), letting consumers walk the per-circuit DER attribution graph. Replaces the retired 0.1.x `bess/feed`, `bess/relative-position`, `pv/feed`, `pv/relative-position`, `evse/feed` entities — derive position from the connection-graph instead.
- **PCS capability** on the panel root — surfaces 17 entities covering import / feed-import / grid-import / off-grid-import / requested-import limits, their enablement enums, and their active boolean siblings. Joined by the relocated `grid-islandable` and `breaker-rating` from the old core node.
- **Per-direction lugs entities** — upstream lugs and downstream lugs are now distinct HA devices ("c192x Upstream Lugs" / "c192x Downstream Lugs"), each with its own info / meter / connection capability entities. The composite-suffix `{serial}_lugs-upstream_imported-energy` unique-ID format is replaced by the cleaner per-device shape.
- **Auto-refresh of device names.** Whenever any device's init→ready edge fires, the integration walks the descendant tree and refreshes HA's device names from the publisher's current values (circuit user-labels in particular). Picks up renames in the SPAN app without a HA restart.
- **Auto-derived multi-panel hierarchy** (SPAN-c7h). When a panel is fed from an upstream sister panel, the integration reads `lugs-up/connection/fed-by-device-id` (a G3P-24911 device-side feature) and sets `via_device` on the downstream panel's HA device automatically. Daisy chains render nested under their feeder in Settings → Devices and in Energy Dashboard Sankeys with no user action — replacing the manually-invoked `link_subpanel` service from 0.1.x. The via_device link re-evaluates on every init→ready cycle, so a publisher-side topology change propagates without a reload.
- **Auto-add of late-arriving descendants.** Slow boot cascades that deliver a descendant's `$state=ready` after initial setup completes (observed on heavily-loaded panels with 19+ circuits) are now caught by a post-setup tree-state hook and registered automatically — no manual reload needed.

### Changed

- **BREAKING — entity unique-ID format** — see release header.
- **BREAKING — required SPAN firmware** — r202627 or later (the release in which G3P-23496 lands). Earlier firmware publishes the flat data model that this integration version no longer understands. Stay on the 0.1.x line until your panel takes the OTA.
- **BREAKING — required ebus-sdk version** — `ebus-sdk >= 0.3.1` (ships the tree-rooted Controller mode and the description-after-state reconcile fix that this integration's discovery flow depends on).
- **Lugs are HA devices now.** In 0.1.x, lug entities hung off the panel device with composite unique-IDs (`{serial}_lugs-upstream_imported-energy`). In 0.2.0 the upstream and downstream lugs each get their own HA device under the panel, with simple property-only entity IDs.
- **Site metering is gone as a sub-device.** The 0.1.x `power-flows` sub-device is now a `power-flows` capability on the panel root device. The four directional entities (`pv-power`, `battery-power`, `grid-power`, `site-power`) live on the panel device itself.
- **Property renames** picked up on the wire (publisher dual-name handling means both the legacy and spec names are recognised during the firmware-side rename roll-out window): `core/software-version` → `info/firmware-version`, `core/door` → `door/state`, `status/vendor-cloud` → `status/cloud-connection`, `circuit/space` → `info/tab-number`, `evse/status` → `status/operational-state`.
- **Circuit boolean trio retired.** `circuit/sheddable` and `circuit/isNeverBackup` are gone (derivable from `priority/shed-priority` + `priority/relay-controllable`). `circuit/alwaysOn` is polarity-flipped to `priority/relay-controllable` (true = relay can be commanded).
- **Energy entity names depend on direction.** Upstream lugs use the friendly "Energy" / "Energy Returned" naming (= grid consumption / grid export) per the README convention. Downstream lugs use literal "Imported Energy" / "Exported Energy" since the semantic flips between directions and SPAN doesn't currently populate the downstream side anyway.
- **BREAKING — `link_subpanel` service removed.** Replaced by the auto-derived multi-panel hierarchy in the Added section above. Existing users on 0.1.x are unaffected (their installs don't run this code); downgrading-then-upgrading isn't a supported path so there's no migration concern.

### Fixed

- **Energy counter monotonicity workaround preserved.** The `total_increasing` energy sensors continue to suppress occasional 0.1 Wh decreases from the firmware ([AN-001](docs/appnote-AN001-energy-counter-monotonicity.md) still applies). Held-value behavior is unchanged from 0.1.x.
- **Active-power firmware bug now publisher-fixed in tree-v1.** SPAN's r202627 firmware publishes circuit `active-power` and PV `nameplate-capacity` in correct units (W rather than the legacy mis-declared kW). The mapper still hard-codes W regardless of what the description says, so a panel that hasn't taken the fix surfaces correctly either way.
- **Lugs connection entities create reliably on first setup** (SPAN-urn). Previously the lugs `connection` mapper bailed when its `info/direction` property hadn't been delivered yet at the moment of initial tree-walk — leaving 8 connection entities silently absent across both lugs devices. Now the mapper falls back to the device-id suffix (`-lugs-up` / `-lugs-dn`) when the property isn't loaded yet, so entities create on the first pass regardless of property-arrival timing.
- **Late-arriving descendant cascades self-heal.** When the SDK delivers a descendant's `$state=ready` after initial setup has timed out — observed on busy panels where retained-message backlog delays `$state` arrival — the post-setup tree-state hook re-registers the descendant automatically.
- **Circuit renames in the SPAN app propagate to HA device names.** Previously the `info/name` property-update callback called `async_get_or_create`, which silently ignores the `name=` kwarg on already-existing devices — so renaming a breaker in the SPAN app updated the entity values but left the HA device label stale. Now the callback routes through `async_update_device` when the device exists (and respects `name_by_user` so user-set custom labels stick).

### Known issues

- **Paho silent subscription dropout** on heavily-loaded connections (panels with 19+ circuits) may leave a single descendant — typically a MID — un-discovered, with no error logged. Manual escape hatch: reload the integration config entry, which creates a fresh paho client. Tracked as ebus-sdk SDK-1v0; doesn't affect entity shape, only delivery reliability of retained messages on the first connection.

### Compatibility

- **Companion tool**: [hass-atlas](https://github.com/electrification-bus/hass-atlas) needs ≥ commit `2fb80d4` for tree-model support. Earlier versions read the 0.1.x flat shape and degrade silently against 0.2.0.

## [0.1.0] — 2026-02-22

Initial alpha release of the SPAN Panel (eBus) Home Assistant custom integration. Targets SPAN MAIN 32 panels running firmware r202603 (the flat data model). See [`README.md`](https://github.com/electrification-bus/span-hass/blob/v0.1.0/README.md) at the `v0.1.0` tag for the entity surface as it shipped — the 0.2.0 release rewrites most of it.

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

[Unreleased]: https://github.com/electrification-bus/span-hass/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/electrification-bus/span-hass/releases/tag/v0.2.0
[0.1.0]: https://github.com/electrification-bus/span-hass/releases/tag/v0.1.0
