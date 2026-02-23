# SPAN Panel (eBus) — Home Assistant Integration

<img src="img/icon2x.png" alt="SPAN Panel icon" width="128" align="right">

A custom [Home Assistant](https://www.home-assistant.io/) integration for [SPAN](https://www.span.io/) smart electrical panels, using the [SPAN eBus API](https://github.com/spanio/SPAN-API-Client-Docs).

Unlike polling-based integrations, span_ebus uses **local push** over MQTT — the panel streams real-time updates directly to Home Assistant with no cloud dependency and no polling interval. Every circuit power change, relay toggle, and energy accumulation arrives instantly via the panel's built-in MQTT broker.

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

- SPAN Panel with eBus firmware (MQTT + REST v2 API enabled)
- Home Assistant 2026.2 or later
- The panel must be reachable on the local network

## Installation

### HACS (recommended)

1. Open HACS in your Home Assistant instance
2. Go to Integrations > three-dot menu > **Custom repositories**
3. Add this repository URL with category **Integration**
4. Install **SPAN Panel (eBus)**
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

### Circuit Entities

For each circuit on your SPAN Panel:

| Entity | Type | Description |
|--------|------|-------------|
| Power | Sensor | Real-time active power (W). Positive = consumption, negative = generation. |
| Energy | Sensor | Cumulative energy consumed (Wh, `total_increasing`) |
| Energy Returned | Sensor | Cumulative energy returned/backfed (Wh, `total_increasing`) |
| Current | Sensor | Real-time current draw (A) |
| Relay | Switch | Circuit breaker relay (on = closed, off = open) |
| Shed Priority | Select | Load-shedding priority level |
| Breaker Rating | Sensor | Breaker amperage (diagnostic) |
| Space | Sensor | Panel space number (diagnostic) |

Circuit names come from the panel's user-assigned labels (set in the SPAN app). The integration waits for these names during setup so entity IDs are stable.

### Panel System Entities

| Entity | Type | Description |
|--------|------|-------------|
| Door | Binary Sensor | Panel door state (tamper class) |
| Ethernet / Wi-Fi / Cellular | Binary Sensor | Network connectivity status |
| Firmware Version | Sensor | Software version (diagnostic) |
| L1 / L2 / L3 Voltage | Sensor | Line voltages (V) |
| Main Breaker Rating | Sensor | Main breaker amperage (diagnostic) |
| Dominant Power Source | Select | Active power source selection |

### Upstream Lug Entities

| Entity | Type | Description |
|--------|------|-------------|
| Energy | Sensor | Grid energy consumed (Wh) |
| Energy Returned | Sensor | Grid energy exported (Wh) |
| Power | Sensor | Grid power (W) |

### Battery Storage (BESS) Entities

Created when a battery system is connected:

| Entity | Type | Description |
|--------|------|-------------|
| State of Charge | Sensor | Battery SOC (%) |
| State of Energy | Sensor | Stored energy (kWh) |
| Connected | Binary Sensor | Battery connectivity |
| Nameplate Capacity | Sensor | Total capacity (Wh, diagnostic) |
| Relative Position | Sensor | UPSTREAM / IN_PANEL (diagnostic) |
| Vendor / Model / Serial | Sensor | Battery metadata (diagnostic) |
| Feed Circuit | Sensor | Which circuit feeds the battery (diagnostic) |

### Solar PV Entities

Created when a PV system is connected:

| Entity | Type | Description |
|--------|------|-------------|
| Nameplate Capacity | Sensor | Array capacity (W, diagnostic) |
| Relative Position | Sensor | UPSTREAM / IN_PANEL (diagnostic) |
| Vendor / Product | Sensor | Inverter metadata (diagnostic) |
| Feed Circuit | Sensor | Which circuit feeds the PV (diagnostic) |

### EV Charger Entities

Created when an EV charger is connected:

| Entity | Type | Description |
|--------|------|-------------|
| Status | Sensor | Charger state |
| Lock State | Binary Sensor | Cable lock |
| Advertised Current | Sensor | Current limit (A) |
| Feed Circuit | Sensor | Which circuit feeds the EVSE (diagnostic) |

## Multi-Panel Support

SPAN panels can be daisy-chained (lead panel + sub-panels). Each panel is set up as a separate config entry and appears as its own device in HA. Sub-devices (circuits, BESS, PV) are grouped under their respective panel.

To establish the parent/child hierarchy in HA's device registry (which enables Sankey chart nesting), use the `link_subpanel` service:

**Settings > Developer Tools > Services:**

```yaml
service: span_ebus.link_subpanel
data:
  sub_serial: "nt-2204-c1c46"
  parent_serial: "nt-2143-c1akc"
```

This sets `via_device_id` in the device registry, which persists across restarts.

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

- **Grid consumption**: Upstream lug "Energy" sensor
- **Return to grid**: Upstream lug "Energy Returned" sensor
- **Solar production**: PV feed circuit "Energy Returned" sensor (if PV is IN_PANEL), or your solar integration's production entity (if PV is UPSTREAM)
- **Battery**: Dedicated battery integration entities (if UPSTREAM), or feed circuit entities (if IN_PANEL)
- **Individual device consumption**: Each circuit's "Energy" sensor

For automated Energy Dashboard configuration with topology-aware overlap detection, see [hass-atlas](https://github.com/dcj/span-hass-tools) — a companion CLI tool that reads your panel topology and intelligently configures the Energy Dashboard, handling multi-vendor setups (SPAN + Tesla Powerwall + Enphase, etc.) without double-counting.

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

All entities use the pattern `{serial}_{node_id}_{property_id}`, for example:

```
nt-2143-c1akc_a1b2c3d4-e5f6_active-power
```

Sub-device identifiers use `{serial}_{node_id}`, linked to the parent panel via `via_device`.

## Known SPAN API Issues

The SPAN firmware has known unit declaration bugs in its Homie `$description` schema:

| Property | Declared Unit | Actual Unit | Affected Node Types |
|----------|--------------|-------------|---------------------|
| `active-power` | kW | **W** (watts) | Circuits |
| `nameplate-capacity` | kW | **W** (watts) | Solar PV |

This integration works around these bugs by overriding the declared units where needed. Other properties (upstream lugs, power-flows) declare correct units.

Additionally, the import/export energy direction convention is not documented in the SPAN API — it was reverse-engineered by observing energy accumulation patterns under known load conditions. See the [Energy Flows](#energy-flows-and-importexport) section above.

## Companion Tools

### hass-atlas

[hass-atlas](https://github.com/dcj/span-hass-tools) is a companion CLI tool for auditing and configuring Home Assistant energy dashboards, area assignments, and device topology. It connects to HA via the WebSocket API and provides:

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

## License

[MIT](LICENSE)
