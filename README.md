# SPAN Panel (eBus) — Home Assistant Integration

A custom [Home Assistant](https://www.home-assistant.io/) integration for [SPAN](https://www.span.io/) smart electrical panels, using the official [SPAN API](https://github.com/spanio/SPAN-API-Client-Docs).

## Features

- **Automatic discovery** via mDNS (`_ebus._tcp`)
- **Local push** updates over MQTT — no cloud, no polling
- Sensors for power, energy, and system status
- Switches for circuit relay control
- Select entities for circuit priority
- Binary sensors for door state and connectivity

## Requirements

- SPAN Panel with eBus firmware (MQTT + REST v2 API enabled)
- Home Assistant 2024.1 or later

## Installation

### HACS (recommended)

1. Open HACS in your Home Assistant instance
2. Go to Integrations → three-dot menu → **Custom repositories**
3. Add this repository URL with category **Integration**
4. Install **SPAN Panel (eBus)**
5. **Restart Home Assistant**

### Manual

1. Copy the `custom_components/span_ebus` folder into your Home Assistant
   `config/custom_components/` directory
2. **Restart Home Assistant**

### Note on first restart

After installing the integration for the first time, you may need to restart Home Assistant **twice** before automatic mDNS discovery works. This is a known limitation of how Home Assistant loads zeroconf service types for custom integrations — the zeroconf listener may start before the custom integration's manifest has been scanned. On the second restart, the integration is already known and discovery will work reliably from that point on.

## Setup

After installation and restart, your SPAN panels should appear automatically under **Settings → Devices & Services** as discovered devices.

If a panel is not discovered automatically, you can add it manually: **Settings → Devices & Services → Add Integration → SPAN Panel (eBus)**, then enter the panel's hostname or IP address.

### Authentication

During setup you will be prompted to authenticate with one of two methods:

- **Passphrase** — Enter the HOP passphrase for your SPAN Panel
- **Door bypass** — Open the panel door and press the door switch 3 times rapidly, then submit the form within 15 minutes

**Tip:** The [SPAN-API-Client-Docs](https://github.com/spanio/SPAN-API-Client-Docs) repository provides command-line tools that can help you prepare credentials before configuring the integration:

```bash
span-discover          # Find SPAN panels on your network
span-auth              # Authenticate and save credentials to ~/.span-auth.json
```

The resulting `~/.span-auth.json` file contains the passphrase and broker credentials for each panel, which you can reference when setting up the integration.

## Entities

For each circuit on your SPAN Panel, the integration creates:

| Entity | Type | Description |
|--------|------|-------------|
| Power | Sensor | Real-time power draw (W) |
| Energy | Sensor | Cumulative energy consumed (Wh) |
| Energy Returned | Sensor | Cumulative energy returned/backfed (Wh) |
| Current | Sensor | Real-time current draw (A) |
| Relay | Switch | Circuit breaker relay (on/off) |
| Shed Priority | Select | Circuit priority level |

Additional system-level entities include door/connectivity binary sensors, firmware/hardware version diagnostics, line voltages, and breaker ratings. Battery storage (BESS), solar PV, and EV charger sub-devices create their own entity sets when present.

## Energy Flows and Import/Export

The SPAN panel uses **import/export terminology from the panel's perspective**, which can be counterintuitive:

| Device | `exported-energy` | `imported-energy` |
|--------|-------------------|-------------------|
| **Circuit** | Energy delivered TO the circuit = **consumption** | Energy flowing FROM circuit back to panel = **backfeed/generation** |
| **Upstream lugs** | Energy sent TO the grid = **solar/battery export** | Energy received FROM the grid = **grid consumption** |

In other words, for a typical load circuit (kitchen, server rack, etc.), the large accumulating value is `exported-energy` (the panel "exports" energy to the circuit). The `imported-energy` value will be near zero unless the circuit has a generator attached.

This integration maps these to user-friendly names:
- Circuit `exported-energy` &rarr; **"Energy"** (primary consumption sensor, use this for the Energy Dashboard)
- Circuit `imported-energy` &rarr; **"Energy Returned"**
- Upstream `imported-energy` &rarr; **"Energy"** (grid consumption)
- Upstream `exported-energy` &rarr; **"Energy Returned"** (grid export)

### Energy Dashboard Configuration

For the **Energy Dashboard**, use these entity mappings:

- **Grid consumption**: Upstream lug `imported-energy` entity (named "Energy")
- **Return to grid**: Upstream lug `exported-energy` entity (named "Energy Returned")
- **Solar production**: Solar PV energy entity
- **Individual device consumption**: Each circuit's `exported-energy` entity (named "Energy")

## Known SPAN API Issues

The SPAN firmware has known unit declaration bugs in its Homie `$description` schema:

| Property | Declared Unit | Actual Unit | Affected Node Types |
|----------|--------------|-------------|---------------------|
| `active-power` | kW | **W** (watts) | Circuits |
| `nameplate-capacity` | kW | **W** (watts) | Solar PV |

This integration works around these bugs by overriding the declared units where needed. Other properties (upstream lugs, power-flows) declare correct units.

## License

[MIT](LICENSE)
