# SPAN Panel (eBus) — Home Assistant Integration

<img src="img/icon2x.png" alt="SPAN Panel icon" width="128" align="right">

A custom [Home Assistant](https://www.home-assistant.io/) integration for [SPAN](https://www.span.io/) smart electrical panels, using the [SPAN eBus API](https://github.com/spanio/SPAN-API-Client-Docs).

Unlike polling-based integrations, span_ebus uses **local push** over MQTT — the panel streams real-time updates directly to Home Assistant with no cloud dependency and no polling interval. Every circuit power change, relay toggle, and energy accumulation arrives instantly via the panel's built-in MQTT broker.

> **Early alpha release.** This integration has been developed over the past 72 hours and has not been tested extensively. It is running on the author's personal Home Assistant server with no known issues, but it is far too soon to conclude that this is a solid and stable integration. Please report any issues on the [GitHub issue tracker](https://github.com/electrification-bus/span-hass/issues).

## Features

- **Automatic discovery** via mDNS (`_ebus._tcp` and `_secure-mqtt._tcp`)
- **Local push** updates over MQTT (TLS) — no cloud, no polling
- **Real-time power** for every circuit (W), updated as values change
- **Cumulative energy** (Wh) for consumption and return per circuit
- **Circuit relay control** — open and close breakers from HA
- **Load-shed priority** — configure circuit shed priority via select entities
- **Battery storage** (BESS) — state of charge, energy, vendor metadata
- **Solar PV** — nameplate capacity, vendor, feed circuit references
- **EV charger** — status, lock state, advertised current
- **Multi-panel support** — daisy-chained panels with parent/child hierarchy
- **Sub-device grouping** — circuits, BESS, PV, EVSE, and power-flow devices appear as separate HA devices under the parent panel
- **Energy Dashboard ready** — entities have correct device/state classes for the HA Energy Dashboard and Sankey charts

## Requirements

- SPAN Panel **MAIN 32**, running firmware **r202627 or later** (the parent/child Homie 5 data model)
- Home Assistant 2026.2 or later
- The panel must be reachable on the local network

> **Pre-r202627 panels**: stay on the [0.1.x line](https://github.com/electrification-bus/span-hass/tree/v0.1.0) until your panel takes the OTA. 0.2.0 was rewritten against the new firmware's tree data model and won't read the flat data model that earlier firmware publishes.

## Installation

### HACS (recommended)

1. Open HACS in your Home Assistant instance
2. Go to Integrations > three-dot menu > **Custom repositories**
3. Enter `https://github.com/electrification-bus/span-hass` and select category **Integration**
4. Click **Add**, then find and install **SPAN Panel (eBus)**
5. **Restart Home Assistant**

### Manual

1. Copy the `custom_components/span_ebus` folder into your Home Assistant `config/custom_components/` directory
2. **Restart Home Assistant**

### Note on first restart

After installing the integration for the first time, you may need to restart Home Assistant **twice** before automatic mDNS discovery works. This is a known limitation of how Home Assistant loads zeroconf service types for custom integrations — the zeroconf listener may start before the custom integration's manifest has been scanned. On the second restart, the integration is already known and discovery will work reliably from that point on.

## Setup

After installation and restart, your SPAN panels should appear automatically under **Settings > Devices & Services** as discovered devices.

If a panel is not discovered automatically, you can add it manually: **Settings > Devices & Services > Add Integration > SPAN Panel (eBus)**, then enter the panel's hostname or IP address.

### Authentication

During setup you will be prompted to authenticate with one of two methods:

- **Passphrase** — Enter the HOP passphrase for your SPAN Panel
- **Door bypass** — Open the panel door and press the door switch 3 times rapidly, then submit the form within 15 minutes

The config flow shows your panel's serial number and firmware version during authentication so you can confirm you're connecting to the right panel.

**Tip:** The [SPAN-API-Client-Docs](https://github.com/spanio/SPAN-API-Client-Docs) repository provides command-line tools that can help you prepare credentials:

```bash
span-discover          # Find SPAN panels on your network
span-auth              # Authenticate and save credentials to ~/.span-auth.json
```

### What happens during setup

1. The integration authenticates via the REST v2 API and receives MQTT broker credentials
2. It downloads the panel's CA certificate for TLS verification
3. An MQTT connection is established to the panel's built-in broker
4. The panel's `$description` (Homie schema) arrives, declaring all nodes and properties
5. The integration waits for the device to reach `ready` state and for circuit names to arrive
6. Entity specs are generated from the description, and devices/entities are registered in HA

If the panel doesn't respond within 30 seconds, setup is retried automatically (HA's `ConfigEntryNotReady` mechanism).

## Entities

In the tree data model each Homie device becomes its own HA device under the panel: lugs (upstream + downstream) + BESS + MID (grandchild of BESS) + PV + EVSE + each circuit. Entities below are grouped by which HA device owns them.

### Panel Device

| Entity | Type | Description |
|--------|------|-------------|
| Vendor / Model / Serial Number / Hardware Version / Firmware Version | Sensor | Identity (diagnostic) |
| eBus Data-Model Version | Sensor | Discriminator string (currently `"1.0"`, diagnostic) |
| Door | Binary Sensor | Panel door state (tamper class) |
| Main Relay | Binary Sensor | Whether the main relay is closed (= passing grid power) |
| Ethernet / Wi-Fi | Binary Sensor | Network connectivity status (diagnostic) |
| Wi-Fi SSID / Postal Code / Time Zone | Sensor | Location + network metadata (diagnostic) |
| Cloud Connection | Sensor | Vendor cloud reachability state (diagnostic) |
| L1 / L2 Voltage | Sensor | Line voltages (V) |
| PV Power / Battery Power / Grid Power / Site Power | Sensor | Panel-level directional power totals (W) — the four flows the Energy Dashboard "Now" Sankey reads |
| PCS Enabled / PCS Active | Binary Sensor | Power Control System master flags (diagnostic) |
| Grid Islandable | Binary Sensor | Whether the panel is wired to island (diagnostic) |
| Main Breaker Rating | Sensor | Main breaker amperage (A, diagnostic) |
| Import Limit / Feed Import Limit / Grid Import Limit / Off Grid Import Limit / Requested Import Limit | Sensor | Current-limit ceilings (A, measurement) |
| (Limit)-Enablement | Sensor | Enum: UNSPECIFIED / UNCONFIGURED / DISABLED / ENABLED (diagnostic) |
| (Limit)-Active | Binary Sensor | Whether each limit is currently being applied (diagnostic) |
| Battery Time Remaining / Time to Priority Shed | Sensor | BTR forecast in minutes — how long the panel can sustain its current load (presence-gated on ≥1 BESS commissioned) |
| Battery Time Remaining at Full Charge / Time to Priority Shed at Full Charge | Sensor | Same forecast assuming the BESS were at 100% SOC right now |
| Shed Forecast Confidence | Sensor | Enum LOW / MEDIUM / HIGH for the BTR forecast (diagnostic) |
| Shed Override | Switch | Force shed-priority shedding (settable; publisher silently ignores out-of-condition writes per spec — only accepted when off-grid + BESS comms degraded) |
| Shed SOC Threshold | Sensor | The SOC% at which priority shedding triggers (diagnostic) |

### Upstream / Downstream Lugs Devices

| Entity | Type | Description |
|--------|------|-------------|
| Direction | Sensor | "upstream" / "downstream" (diagnostic — disambiguates the two lugs devices) |
| L1 Current / L2 Current | Sensor | Per-leg current (A) |
| Power | Sensor | Active power (W) — positive = power flowing into the panel from this lug |
| Energy | Sensor | Cumulative energy through this lug (Wh, `total_increasing`). On upstream = grid consumption (the dominant counter). |
| Energy Returned | Sensor | Cumulative energy in the opposite direction. On upstream = export to grid. |
| Connection Count | Sensor | Number of physical units this lug aggregates (diagnostic) |
| Fed By Device / Feeds Device | Sensor | Homie device-id pointing at the upstream / downstream peer (diagnostic) |
| Fed By Device Type / Feeds Device Type | Sensor | The peer's device-class string (diagnostic) |
| Upstream / Downstream Connection Problem | Binary Sensor | PROBLEM class — on when the peer's status is LOST or DEGRADED |

Energy-name asymmetry: upstream lugs use friendly "Energy" / "Energy Returned"; downstream lugs use literal "Imported Energy" / "Exported Energy" since SPAN doesn't populate the downstream side today and the semantic flips between directions.

### Circuit Devices

For each circuit on your SPAN Panel:

| Entity | Type | Description |
|--------|------|-------------|
| Name | Sensor | Circuit user-label from the SPAN app (diagnostic) |
| Breaker Rating | Sensor | Breaker amperage (A, diagnostic) |
| Tab Number | Sensor | Panel space number (diagnostic — renamed from `space` in 0.1.x) |
| Dipole | Binary Sensor | Two-pole vs single-pole (diagnostic) |
| Current | Sensor | Real-time current draw (A) |
| Power | Sensor | Real-time active power (W). Positive = consumption, negative = generation (backfeed from a PV-feeding circuit). |
| Energy | Sensor | Cumulative energy consumed (Wh, `total_increasing`). The dominant counter for load circuits. |
| Energy Returned | Sensor | Cumulative energy returned/backfed (Wh, `total_increasing`). Near zero except on PV-feeding circuits. |
| Relay | Switch | Circuit breaker relay (on = closed, off = open). Gated `$settable` per spec — non-settable on circuits commissioned as locked-on or locked-off. |
| Relay Requester | Sensor | Enum showing who last commanded the relay (USER / LOAD_SHED / PCS / CONFIGURATION / FAULT / NONE / UNKNOWN, diagnostic) |
| Shed Priority | Select | Load-shedding priority (UNKNOWN / OFF_GRID / SOC_THRESHOLD / NEVER). Gated `$settable` — non-settable when commissioned as permanent OFF_GRID. |
| PCS Managed / PCS Priority | Binary Sensor / Sensor | Whether the PCS is managing this circuit + its priority (diagnostic) |
| Relay Controllable | Binary Sensor | Whether the relay can be commanded (diagnostic — polarity-flipped successor to 0.1.x's `alwaysOn`) |
| Feeds Device / Feeds Device Type / Feeds Connection Problem / Feeds Count | Sensor / Binary Sensor | Connection capability — populated when the circuit is commissioned as feeding a specific DER (PV, IN_PANEL BESS, EVSE) |

### Battery Storage (BESS) Device

Created per commissioned battery. Sub-devices the BESS may proxy (Tesla Powerwall, etc.) appear as their own grandchild devices under this one (see MID below).

| Entity | Type | Description |
|--------|------|-------------|
| Vendor / Product / Model / Serial Number / Firmware Version | Sensor | Identity (diagnostic) |
| Nameplate Capacity | Sensor | Vendor-rated storage capacity (kWh, ENERGY_STORAGE device class, diagnostic) |
| State of Charge | Sensor | Battery SOC (%, BATTERY device class) |
| State of Energy | Sensor | Currently-stored energy (kWh, ENERGY_STORAGE device class) |

### Microgrid Interconnect Device (MID)

Every commissioned BESS gets a synthesized MID grandchild — SPAN synthesizes the MID for spec conformance even when the vendor hardware (e.g. Tesla Powerwall) doesn't expose a separable MID. Lives as a child of the BESS in the HA device tree, not directly under the panel.

| Entity | Type | Description |
|--------|------|-------------|
| Vendor / Product / Model / Serial Number / Hardware Version / Firmware Version | Sensor | Mostly null on synthesized MIDs (diagnostic) |
| Islanding State | Sensor | ON_GRID / OFF_GRID / UNKNOWN |
| Grid State | Sensor | UP / DOWN / DEGRADED / UNKNOWN — utility-side health summary |
| Grid Forming Entity | Sensor | What's currently forming the grid: `"GRID"` when grid-tied, the BESS device-id when islanded (diagnostic) |

### Solar PV Device

Created per inverter when a PV system is commissioned.

| Entity | Type | Description |
|--------|------|-------------|
| Vendor / Product / Serial Number / Firmware Version | Sensor | Identity. Serial may be null for SPAN G2 deployments where the cloud-shadow doesn't surface it (diagnostic) |
| Nameplate Capacity | Sensor | Array capacity (W, POWER device class, diagnostic) |

### EV Charger Device

Created per EVSE when one is commissioned.

| Entity | Type | Description |
|--------|------|-------------|
| Vendor / Product / Part Number / Serial Number / Firmware Version | Sensor | Identity (diagnostic) |
| Status | Sensor | EVSE operational state (renamed from `evse/status` in 0.1.x) |
| Lock State | Sensor | Cable lock state (read-only per spec) |
| Advertised Current | Sensor | Current the EVSE is offering to the car (A) |
| User Max Charge Current / Max Charge Current | Sensor | User-imposed and hardware ceilings (A, diagnostic) |

## Upgrading from 0.1.x

0.2.0 is a clean-cut migration to the SPAN firmware r202627 parent/child Homie 5 data model. **Entity unique-IDs are reset** — every entity gets a new ID reflecting the new tree shape. The old 0.1.x entities will appear in HA's registry as "unavailable" orphans after upgrade.

**Recommended path**: delete and re-add each panel config entry after the upgrade. HA removes all devices and entities the integration created when you delete a config entry, then re-adding via the standard mDNS-discovered flow (passphrase or door bypass) creates the new entities fresh. ~2 min per panel.

**Alternative**: just re-enable the existing config entries. The new entities appear alongside the old orphans (which remain in the registry as `unavailable`). You can then clean up orphans manually via Settings > Devices & Services > each device > Entities > delete unavailable.

Either way, **Energy Dashboard configuration must be rebuilt** — the unique-IDs the dashboard references no longer have backing entities. Recommended: snapshot your pre-upgrade dashboard config with [hass-atlas](https://github.com/electrification-bus/hass-atlas) before upgrading, then re-run hass-atlas against the new entity surface after upgrade.

## Multi-Panel Support

SPAN panels can be daisy-chained (lead panel + sub-panels). Each panel is set up as a separate config entry and appears as its own device in HA. Sub-devices (circuits, BESS, PV) are grouped under their respective panel.

In **0.2.x** with SPAN firmware r202627 or later, the cross-panel `via_device` hierarchy is **derived automatically** from each panel's `lugs-up/connection/fed-by-device-id` triplet — no manual configuration required. When a downstream panel's upstream feed is published as another `distribution-enclosure` device, this integration links the downstream panel under the upstream one at setup time (and re-evaluates on every init→ready cycle if the publisher updates it).

Result: daisy chains render correctly under Settings → Devices and in the Energy Dashboard Sankey with no service calls.

## Energy Flows and Import/Export

The SPAN panel uses **import/export terminology from the panel's perspective**, which can be counterintuitive:

| Device | `exported-energy` | `imported-energy` |
|--------|-------------------|-------------------|
| **Circuit** | Energy delivered TO the circuit = **consumption** | Energy flowing FROM circuit back to panel = **backfeed/generation** |
| **Upstream lugs** | Energy sent TO the grid = **solar/battery export** | Energy received FROM the grid = **grid consumption** |

For a typical load circuit (kitchen, server rack, etc.), the large accumulating value is `exported-energy` (the panel "exports" energy to the circuit). The `imported-energy` value will be near zero unless the circuit has a generator attached.

This integration maps these to user-friendly entity names:

- Circuit `exported-energy` → **"Energy"** (primary consumption sensor)
- Circuit `imported-energy` → **"Energy Returned"**
- Upstream `imported-energy` → **"Energy"** (grid consumption)
- Upstream `exported-energy` → **"Energy Returned"** (grid export)

### Power Sign Convention

Circuit `active-power` is **negated** by the integration so that positive values represent consumption. This matches Home Assistant's convention for `device_consumption` stat_rate in the Energy Dashboard "Now" (power Sankey) tab.

Raw SPAN values: negative = consumption, positive = generation (backfeed from PV).
After negation: positive = consumption, negative = generation.

### Energy Dashboard Configuration

For the **Energy Dashboard**, use these entity mappings:

- **Grid consumption**: Upstream Lugs device, "Energy" sensor
- **Return to grid**: Upstream Lugs device, "Energy Returned" sensor
- **Solar production**: PV-feeding circuit's "Energy Returned" sensor (if PV is IN_PANEL — derive position from the circuit's `feeds-device-id` pointing at the PV device), or your solar integration's production entity (if PV is UPSTREAM — derive position from `lugs-up/connection/fed-by-device-id` pointing at the PV)
- **Battery**: Dedicated battery integration entities (if BESS is UPSTREAM), or the BESS-feeding circuit's energy entities (if BESS is IN_PANEL). The Upstream Lugs `Fed By Device` entity points at the BESS Homie device-id when the BESS is upstream of the panel.
- **Individual device consumption**: Each circuit device's "Energy" sensor

> **Position is derived from the connection graph in 0.2.0**, not from the retired `relative-position` / `feed` entities. The lugs `connection` capability and per-circuit `connection` capability publish `feeds-device-id` / `fed-by-device-id` pointers that resolve who-feeds-what at runtime. Consumers like hass-atlas walk this graph automatically.

For automated Energy Dashboard configuration with topology-aware overlap detection, see [hass-atlas](https://github.com/electrification-bus/hass-atlas) — a companion CLI tool that reads your panel topology and intelligently configures the Energy Dashboard, handling multi-vendor setups (SPAN + Tesla Powerwall + Enphase, etc.) without double-counting. **Requires hass-atlas commit `2fb80d4` or later** for span-hass 0.2.0; earlier versions read the 0.1.x flat shape and degrade silently.

## Architecture

### Communication Flow

```
SPAN Panel                          Home Assistant
┌──────────────────┐                ┌──────────────────────┐
│  REST v2 API     │◄── config ────►│  Config Flow         │
│  (auth, status)  │    flow only   │  (api_client.py)     │
│                  │                │                      │
│  MQTT Broker     │◄── push ──────►│  ebus-sdk Controller │
│  (port 8883/TLS) │    updates     │  (span_panel.py)     │
│                  │                │        │              │
│  Homie Protocol  │                │        ▼              │
│  $description    │                │  Entity Callbacks    │
│  $state          │                │  (sensor, switch,    │
│  properties      │                │   binary_sensor,     │
└──────────────────┘                │   select)            │
                                    └──────────────────────┘
```

- **REST v2 API** is used only during the config flow for authentication, status checks, and CA certificate download
- **MQTT** handles all runtime communication — property updates, relay commands, and availability
- **Homie Convention** provides the self-describing schema (`$description`) that the integration uses to auto-generate entities
- **ebus-sdk** manages the MQTT connection, device discovery, and property tracking

### Thread Safety

The ebus-sdk's MQTT callbacks run on the paho-mqtt background thread. The integration bridges to Home Assistant's asyncio event loop using `hass.loop.call_soon_threadsafe()`, ensuring all HA operations happen on the correct thread.

### Entity Lifecycle

1. MQTT `$description` arrives declaring all nodes and properties
2. `node_mappers.py` maps each Homie property to an `EntitySpec` (platform, device class, units, etc.)
3. Platforms create entities from specs; each entity registers a property callback
4. When a property value changes on MQTT, the callback updates the entity state and calls `async_write_ha_state()`
5. `should_poll = False` — entities never poll, they only update on push

### Unique ID Format

All entities use the 4-segment pattern `{panel-serial}_{device-id}_{capability}_{property-id}`, for example:

```
nt-2024-a1b2c_nt-2024-a1b2c-lugs-up_meter_imported-energy   # upstream-lug grid-consumption counter
nt-2024-a1b2c_a1b2c3d4-e5f6_meter_active-power              # circuit power
nt-2024-a1b2c_nt-2024-a1b2c-tg-pw-1234_soc_soc              # BESS state-of-charge
nt-2024-a1b2c_nt-2024-a1b2c-tg-pw-1234-mid_grid_islanding-state  # MID grandchild
```

The panel-serial prefix is intentional even when the device-id segment already contains the panel serial — it keeps unique-IDs globally distinct across multi-panel installs, and circuit device-ids are bare UUIDs (no panel-serial prefix in the Homie device-id itself), so the panel-serial here is the only thing keeping their unique-IDs distinct across panels.

HA device identifiers use `{panel-serial}_{device-id}`, linked up the tree via `via_device` (lugs / BESS / PV / EVSE / circuit → panel root; MID → BESS).

## Known SPAN API Issues

| Property | Issue | Status |
|----------|-------|--------|
| `active-power` (circuits) | Legacy firmware declared `unit="kW"` but actual values were in watts | **Fixed in r202627+ tree data model.** The mapper still hard-codes W regardless, so a panel that hasn't taken the fix surfaces correctly. |
| `nameplate-capacity` (PV) | Same kW-but-actually-W declaration | **Fixed in r202627+.** Same hard-coded-W fallback. |

This integration works around these bugs by overriding the declared units where needed. Other properties (upstream lugs, power-flows) declare correct units.

Additionally, the import/export energy direction convention is not documented in the SPAN API — it was reverse-engineered by observing energy accumulation patterns under known load conditions. See the [Energy Flows](#energy-flows-and-importexport) section above.

## Companion Tools

### hass-atlas

[hass-atlas](https://github.com/electrification-bus/hass-atlas) is a companion CLI tool for auditing and configuring Home Assistant energy dashboards, area assignments, and device topology. It connects to HA via the WebSocket API and provides:

- **Topology-aware Energy Dashboard configuration** — reads SPAN panel metadata (battery position, solar vendor, feed circuits) and cross-references with other integrations to build correct configurations without double-counting
- **Multi-panel Sankey hierarchy** — configures `included_in_stat` relationships so the Energy Dashboard shows energy flowing through daisy-chained panels
- **Area management** — bulk-assigns circuit devices to HA areas
- **Energy audit** — finds stale references and broken configurations

### SPAN API Client Tools

The [SPAN-API-Client-Docs](https://github.com/spanio/SPAN-API-Client-Docs) repository provides command-line tools for panel discovery, authentication, and MQTT debugging:

```bash
span-discover          # Find SPAN panels via mDNS
span-auth              # Authenticate and save credentials
span-mqtt-sub          # Subscribe to MQTT topics with saved credentials
```

## Design Decisions and Future Considerations

### MQTT QoS 1

The integration subscribes to MQTT topics at **QoS 1** (at-least-once) rather than QoS 2 (exactly-once). For continuously-updating sensor data, QoS 1 is perfectly adequate — a duplicate or missed power reading is harmless since the next update arrives within seconds. QoS 2's four-step handshake (PUBREC/PUBREL/PUBCOMP) adds complexity, and paho-mqtt's implementation stores in-flight QoS 2 messages with no timeout, creating a potential accumulation vector if any step is lost. If future firmware or use cases require guaranteed delivery (e.g., for command acknowledgments), QoS can be changed via `MQTT_QOS` in `const.py`. Future options for making this configurable without a code change include reading the `EBUS_HOMIE_MQTT_QOS` environment variable (already supported by the SDK) or adding a QoS setting to the integration's HA options flow.

### Memory Diagnostics

The integration includes periodic memory diagnostics (every 30 minutes) that log peak RSS, tracemalloc-traced memory, paho-mqtt queue depths, and top memory allocators. This is intentional — the integration drives hundreds of entities with continuous MQTT updates, making it a significant memory consumer. The diagnostics have near-zero overhead and have already proven invaluable for diagnosing system-level issues on resource-constrained hardware like the HA Yellow.

## Development

```bash
poetry install
poetry run pytest tests/ -v             # 96 tests
poetry run mypy custom_components/span_ebus/
poetry run ruff check custom_components/span_ebus/
```

### Dependencies

- [ebus-sdk](https://pypi.org/project/ebus-sdk/) — MQTT client for the SPAN eBus/Homie protocol
- Home Assistant core (dev dependency for testing)

## Releases

See [CHANGELOG.md](CHANGELOG.md). The integration is in early alpha — entity unique-IDs and the config-flow shape are intended to be stable, but expect entity additions and refinements as more SPAN deployments come online.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to file Discussions, Issues, and pull requests. Changes to MQTT transport, Homie discovery, or device-tree semantics belong in [`ebus-sdk`](https://github.com/electrification-bus/python-sdk) rather than here — this repo is the Home Assistant adapter layer on top. Normative behavior tracks the [Electrification Bus specification](https://github.com/electrification-bus/specification).

## License

[MIT](LICENSE)
