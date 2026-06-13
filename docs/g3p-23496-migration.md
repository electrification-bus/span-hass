# G3P-23496 Migration: Flat → Parent/Child Homie 5

> **Status:** Design — implementation not yet started
> **Date:** 2026-06-13
> **Tracking:** SPAN-d5i (umbrella), SPAN-byk (this design doc)
> **Affects:** All `span_ebus` entities; this is a major version bump (`0.1.x` → `0.2.0`)
> **Pre-requires:** SPAN firmware **r202627** or later (the release in which G3P-23496 lands)

## Summary

The SPAN device firmware is moving from a **flat** Homie publication (one Homie device per panel, with many nodes) to a **parent/child tree** publication (panel root device + N child devices + grandchildren), per the [Electrification Bus specification](https://github.com/electrification-bus/specification) and [Homie 5](https://homieiot.github.io). After firmware r202627, the flat publication is gone (a `ebus-panel-adapter-flat` rollback adapter exists but is opt-in and not the OTA default). This integration must be rewritten against the tree shape — there is no graceful coexistence path, and unique-IDs are not preserved.

## Decisions

These are the non-negotiables that the rest of the design flows from:

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | **Clean cut.** No `data-model-version` discriminator, no flat-fallback code path. The integration version bumps `0.1.x` → `0.2.0` and requires SPAN firmware r202627+. | The integration's homeowner-user has already OTA'd. No other known deployments. Maintaining two codepaths for theoretical pre-r202627 users is not worth the cost. |
| 2 | **Unique-IDs reset.** Entities get a new `unique_id` scheme reflecting the tree (`{serial}_{device-id}_{capability}_{property-id}`). No translation table maps old → new. Users will see orphaned entities in the registry after upgrade and clean them up once. | Designing a translation table that survives `core → info/status/door/meter/pcs` splits and `circuit/sheddable → connection/feeds-device-status` reshapes is more work than it's worth for a one-user upgrade. |
| 3 | **Brand images stay SPAN.** No per-child-device branding via the `brand/` directory. Per-device manufacturer info comes from each child device's `info/vendor-name` + `info/model` (Tesla on a Powerwall BESS, Enphase on PV, etc.) flowing into HA's `device_info.manufacturer`/`model`. | HA's `brand/` mechanism is at the integration level, not per-device. Per-device manufacturer info is already handled correctly via `device_info`. |
| 4 | **`link_subpanel` service unchanged.** Daisy-chained SPAN panels are each independent Homie root devices on their own MQTT brokers; SPAN does not publish "panel A is downstream of panel B" anywhere in MQTT. The `via_device` link in HA's device registry remains the user's only way to express that physical hierarchy for the Energy Dashboard Sankey. | Homie 5 `$parent`/`$children` semantics describe *within-panel* hierarchy (panel → BESS → MID, panel → circuit). They don't describe *between-panel* feedthrough on SPAN. |

## Compatibility statement (for README/CHANGELOG)

```
span_ebus 0.2.0 requires SPAN firmware r202627 or later. The flat-publication
data model used by earlier firmware is no longer supported. Users on older
firmware should stay on span_ebus 0.1.x until they take the OTA. The 0.2.0
upgrade is not entity-id-compatible with 0.1.x — expect orphaned entities in
the HA registry after upgrade, which can be removed via Settings > Devices &
Services > <panel> > Entities.
```

## Structural diff

**Before** (flat — one Homie device per panel):

```
ebus/5/<panel-serial>/                              ← ONE Homie device
  $description, $state, $nodes: core, lugs-upstream, lugs-downstream,
                                bess, pv, evse, power-flows, pcs, <circuit-uuid>...
```

**After** (tree — many Homie devices per panel, linked by $parent/$children):

```
ebus/5/<panel-serial>/                              ← panel root (type=distribution-enclosure)
  capabilities: info, door, meter, status, pcs, power-flows,
                shed-forecast*, shed*                (*present iff ≥1 BESS commissioned)
ebus/5/<panel-serial>-lugs-up/                      ← upstream-lugs child (type=lugs)
  capabilities: info, meter, connection
ebus/5/<panel-serial>-lugs-dn/                      ← downstream-lugs child (type=lugs)
  capabilities: info, meter, connection
ebus/5/<panel-serial>-<bess-serial>/                ← BESS child (type=bess), one per commissioned BESS
  capabilities: info, soc
ebus/5/<panel-serial>-<bess-serial>-mid/            ← MID grandchild of BESS (type=mid), synthesized
  capabilities: info, grid
ebus/5/<panel-serial>-<pv-id>/                      ← PV child (type=pv), one per inverter; pv-id = sanitized vendor serial or "pv-1"
  capabilities: info
ebus/5/<panel-serial>-<drive-serial>/               ← EVSE child (type=evse), one per EVSE
  capabilities: info, status, switch, meter, config
ebus/5/<circuit-uuid>/                              ← circuit child (type=circuit), one per circuit (bare UUID, parent=panel root)
  capabilities: info, meter, switch, priority, connection
```

The integration walks the tree via `ebus_sdk.Controller.get_root_devices()` → filter to panel-type roots → `Controller.get_descendants(panel_root_id)` and maps each descendant to its own HA device.

## SDK bump

| File | Current | Required |
|------|---------|----------|
| `custom_components/span_ebus/manifest.json` | `"ebus-sdk>=0.1.1"` | `"ebus-sdk>=0.3.0"` |
| `pyproject.toml` | `ebus-sdk = "^0.1.0"` | `ebus-sdk = "^0.3.0"` |

0.2.0 added the parent/child `Device` + `Controller` API; 0.2.1 added the public `sanitize_homie_id` helper; 0.2.2 fixed an iteration-race in `Device.publish_nodes()` / `Device.refresh_tree()` that triggered on broker reconnect; **0.3.0 added the tree-rooted discovery mode this integration depends on** (`Controller(root_device_id=<id>)`, auto-subscribes to descendants via the parent's `$description.children` reconcile, gated on the parent's `$state` init→ready edge — see SDK-o1h).

Key SDK surface this integration now depends on:

- `Controller(root_device_id=<panel-serial>)` — tree-rooted discovery mode. Subscribes to the panel root's four topic patterns and auto-walks descendants. The reconcile fires on transitions into `ready`, so a mid-flight panel structural change (panel adds a circuit, drops a BESS) is observed cleanly without the integration polling for it.
- `Controller.set_on_device_removed_callback(cb)` — fires (leaves-first) when a descendant disappears from the parent's `$description.children`. The integration uses this to retire the corresponding HA device + entities.
- `Controller.get_root_devices()`, `get_root(device_id)`, `get_children(device_id)`, `get_descendants(device_id)` — tree navigation
- `Controller.get_effective_state(device_id)` + `HOMIE_EFFECTIVE_STATE_TABLE` — Homie 5 state-precedence rule, so when the panel root is `init`/`disconnected`/`lost`, every descendant is effectively the same state without each republishing
- `Device.refresh_tree()` — recursive republish on reconnect; the integration's reconnect path triggers a re-walk
- `ebus_sdk.sanitize_homie_id(value)` — coerces vendor-supplied strings to Homie-legal id segments

## File-by-file impact

| File | Lines (current) | Impact |
|------|-----------------|--------|
| `api_client.py` | 171 | Light — REST v2 client is config-flow-only and unchanged by the data-model migration. |
| `config_flow.py` | 216 | Light — auth + cert paths unchanged. |
| `const.py` | 36 | Minor — add capability-name constants, retire `NODE_TYPE_*` flat-model strings. |
| `entity_base.py` | 101 | Minor — `unique_id` format changes (decision #2); ancestor/effective-state plumbing for HA availability. |
| `span_panel.py` | 267 | **Significant** — `Controller` constructor switches from `device_id=<panel-serial>` (today's single-device mode) to `root_device_id=<panel-serial>` (SDK 0.3.0 tree-rooted mode); each descendant becomes its own HA `DeviceInfo` with `via_device=` set to its Homie `$parent`; new `on_device_removed` handler retires HA devices/entities when a descendant disappears. |
| `node_mappers.py` | 891 | **Largest rewrite** — see capability table below. Restructured from "9 mappers keyed by device-type string" to "mappers keyed by `(device-class, capability)`". |
| `sensor.py` / `switch.py` / `select.py` / `binary_sensor.py` | ~290 total | Light — platforms drive off `EntitySpec` and remain mostly unchanged; the platform routing inside `entities_from_description` updates to walk descendants. |
| `services.py` | 55 | None — `link_subpanel` unchanged (decision #4). |
| `__init__.py` | 405 | Moderate — entry-setup walks the tree; memory diagnostics unchanged. |
| `tests/` | (14 files) | Fixtures rebuilt against tree-v1 snapshot JSONs (see "Reference snapshots"). |

## Property-mapping table

Distilled from `~/projects/span.io/shadow-repo/device/doc/g3p-23496-property-mapping.csv` (the canonical source — read it directly when implementing; this table is a navigational summary, not a substitute).

Conventions used in the table:

- **HA platform** = which HA platform the entity goes on (`sensor`, `switch`, `select`, `binary_sensor`). `—` = not exposed (internal, derivable, omitted).
- **Notes** column flags settable, gated, retired, polarity-flipped, etc.

### Panel root — `distribution-enclosure` device

#### `info` capability (panel identity, read-only)

| Property | HA platform | Old location | Notes |
|----------|-------------|--------------|-------|
| `vendor-name` | sensor (diag) | `core/vendor-name` | unchanged |
| `model` | sensor (diag) | `core/model` | unchanged |
| `serial-number` | sensor (diag) | `core/serial-number` | unchanged |
| `hardware-version` | sensor (diag) | `core/hardware-version` | unchanged |
| `firmware-version` | sensor (diag) | `core/software-version` | **renamed** |
| `data-model-version` | sensor (diag) | — | **net-new**; value `"1.0"` |

#### `door` capability

| Property | HA platform | Old location | Notes |
|----------|-------------|--------------|-------|
| `state` | binary_sensor (tamper) | `core/door` | **renamed**: `door` → `state` |

#### `meter` capability (panel-level voltages, lug-mirrored energies)

| Property | HA platform | Old location | Notes |
|----------|-------------|--------------|-------|
| `l1-voltage` | sensor (V) | `core/l1-voltage` | unchanged |
| `l2-voltage` | sensor (V) | `core/l2-voltage` | unchanged |
| `l1-current` / `l2-current` / `active-power` / `imported-energy` / `exported-energy` | — | `core/*` | internal-only; already published via `lugs-*` nodes — do NOT create duplicate entities |

#### `status` capability (network + cloud + relay)

| Property | HA platform | Old location | Notes |
|----------|-------------|--------------|-------|
| `relay` | binary_sensor | `core/relay` | unchanged value |
| `ethernet` | binary_sensor (connectivity) | `core/ethernet` | unchanged |
| `wifi` | binary_sensor (connectivity) | `core/wifi` | unchanged |
| `wifi-ssid` | sensor (diag) | `core/wifi-ssid` | unchanged |
| `cloud-connection` | binary_sensor (connectivity) | `core/vendor-cloud` | **renamed** |
| `postal-code` | sensor (diag) | `core/postal-code` | unchanged |
| `time-zone` | sensor (diag) | `core/time-zone` | unchanged |

#### `pcs` capability (Power Control System — joined by relocated panel-level properties)

| Property | HA platform | Old location | Notes |
|----------|-------------|--------------|-------|
| `grid-islandable` | binary_sensor | `core/grid-islandable` | **moved** from core |
| `breaker-rating` | sensor (A, diag) | `core/breaker-rating` | **moved** from core |
| `enabled` / `active` | binary_sensor | `pcs/*` | unchanged |
| `import-limit` / `feed-import-limit` / `grid-import-limit` / `off-grid-import-limit` / `requested-import-limit` | sensor (W) | `pcs/*` | unchanged |
| `*-enablement` / `*-active` (boolean siblings of the limits above) | binary_sensor | `pcs/*` | unchanged |

#### `power-flows` capability (panel-level directional totals)

| Property | HA platform | Old location | Notes |
|----------|-------------|--------------|-------|
| `pv` / `battery` / `grid` / `site` | sensor (W) | `power-flows/*` | unchanged |

#### `shed-forecast` capability — **net-new**; presence-gated on ≥1 BESS

| Property | HA platform | Notes |
|----------|-------------|-------|
| `total-time-remaining` | sensor (minutes) | "battery time remaining" total |
| `time-to-priority-shed` | sensor (minutes) | time until priority-shed triggers |
| `full-charge-total-time-remaining` | sensor (minutes) | if BESS were fully charged now |
| `full-charge-time-to-priority-shed` | sensor (minutes) | if BESS were fully charged now |
| `confidence` | sensor (enum LOW/MEDIUM/HIGH) | forecast confidence |

#### `shed` capability — **net-new**; presence-gated on ≥1 BESS

| Property | HA platform | Notes |
|----------|-------------|-------|
| `override` | switch (settable) | static `$settable=true`; adapter silently ignores out-of-condition writes |
| `soc-threshold` | sensor (%, diag) | read-only on SPAN today |

#### Retired from `core` (no replacement entity)

- `dominant-power-source` — split into `mid/grid/grid-forming-entity` (read-only string on MID grandchild) + `shed/override` (settable bool on panel root). No direct successor entity; the read-only piece lives on the MID, the settable piece lives on the panel.

### Upstream-lugs child — `lugs` device (`<panel-serial>-lugs-up`)

| Capability | Property | HA platform | Old location | Notes |
|------------|----------|-------------|--------------|-------|
| `info` | `direction` | sensor (diag) | `lugs/direction` | moved |
| `meter` | `l1-current` / `l2-current` / `active-power` / `imported-energy` / `exported-energy` | sensor | `lugs-upstream/*` | moved |
| `connection` | `fed-by-device-id` | sensor (diag) | `lugs/feed` (was free-form string) | **net-new** structured; restructured from old `feed` |
| `connection` | `fed-by-device-type` | sensor (diag) | — | **net-new** |
| `connection` | `fed-by-device-status` | sensor (enum OK/LOST/DEGRADED) | — | **net-new** |
| `connection` | `count` | sensor (diag) | — | **net-new**; only populated when upstream aggregates multiple physical units |

### Downstream-lugs child — `lugs` device (`<panel-serial>-lugs-dn`)

Same `info`/`meter` capabilities as upstream-lugs. `connection/feeds-device-*` triplet is spec'd but **SPAN does not publish today** — the integration should expect these properties absent and handle gracefully.

### BESS child — `bess` device (`<panel-serial>-<bess-serial>`)

| Capability | Property | HA platform | Old location | Notes |
|------------|----------|-------------|--------------|-------|
| `info` | `vendor-name` | sensor (diag) | `bess/vendor-name` | moved |
| `info` | `product-name` | sensor (diag) | `bess/product-name` | moved |
| `info` | `model` | sensor (diag) | `bess/model` | moved |
| `info` | `serial-number` | sensor (diag) | `bess/serial-number` | moved; also drives the Homie device-id segment |
| `info` | `firmware-version` | sensor (diag) | `bess/software-version` | **renamed** |
| `info` | `nameplate-capacity` | sensor (Wh, diag) | `bess/nameplate-capacity` | moved |
| `soc` | `soc` | sensor (%) | `bess/soc` | moved |
| `soc` | `soe` | sensor (kWh) | `bess/soe` | moved |

#### Retired from `bess`

- `relative-position` — derive from `connection` records (IN_PANEL ⇔ a circuit's `connection/feeds-device-id` references this BESS; UPSTREAM ⇔ `<panel>-lugs-up/connection/fed-by-device-id` references this BESS)
- `feed` — replaced by structured `connection/*` triplet on the enclosure-side device
- `connected` — replaced by `feeds-device-status` / `fed-by-device-status` enum on the enclosure-side `connection` capability (the bool collapses into the OK/LOST/DEGRADED enum)
- `grid-state` — relocated to MID grandchild as `mid/grid/islanding-state` (value preserved, enum check pending)

### MID grandchild — `mid` device (`<panel-serial>-<bess-serial>-mid`), child of BESS

Every commissioned BESS gets a synthesized MID grandchild — Tesla Powerwall etc. don't expose a separable MID, so SPAN synthesizes one for spec conformance. All-net-new on the wire (but `islanding-state` carries the value of old `backup/grid-state`).

| Capability | Property | HA platform | Notes |
|------------|----------|-------------|-------|
| `info` | `vendor-name` / `serial-number` / `product-name` / `model` / `firmware-version` / `hardware-version` | sensor (diag) | net-new; mostly null on synthesized MIDs |
| `grid` | `islanding-state` | sensor (enum) | net-new; value from old `bess/grid-state` |
| `grid` | `grid-state` | sensor (enum) | net-new |
| `grid` | `grid-forming-entity` | sensor (diag) | net-new; `"GRID"` when grid-tied, BESS Homie device-id when islanded |

### PV child — `pv` device (`<panel-serial>-<pv-id>`)

| Capability | Property | HA platform | Old location | Notes |
|------------|----------|-------------|--------------|-------|
| `info` | `vendor-name` | sensor (diag) | `pv/vendor-name` | moved |
| `info` | `product-name` | sensor (diag) | `pv/product-name` | moved |
| `info` | `serial-number` | sensor (diag) | `pv/serial-number` | moved; null for SPAN G2 deployments (cloud-shadow gap) |
| `info` | `firmware-version` | sensor (diag) | `pv/software-version` | **renamed** |
| `info` | `nameplate-capacity` | sensor (W, diag) | `pv/nameplate-capacity` | moved; firmware-bug unit override (declared kW, actual W) still required |

#### Retired from `pv`

- `relative-position` — derive from `connection` records (same rule as BESS)
- `feed` — replaced by structured `connection/feeds-device-id` triplet on the feeding circuit
- `pv/meter/active-power` and `pv/meter/exported-energy` — **omitted from v1**. Two cases: (a) BESS-attached PV is metered by the BESS's data model upstream of us; (b) PV-on-SPAN-circuit is metered via the feeding `<circuit-uuid>/meter/active-power`, attributed to the PV by following `circuit/connection/feeds-device-id`. The existing **Generation Power** virtual entity (PV-feeding circuit's negative `active-power` surfaced as a positive `power` sensor — see commit 849a8f0) needs to be re-derived in this new world: read the feeding-circuit's `meter/active-power`, identify the connection target as a PV device, surface the synthesized entity attributed to the PV child device.

### EVSE child — `evse` device (`<panel-serial>-<drive-serial>`)

| Capability | Property | HA platform | Old location | Notes |
|------------|----------|-------------|--------------|-------|
| `info` | `vendor-name` / `product-name` / `part-number` / `serial-number` | sensor (diag) | `evse/*` | moved |
| `info` | `firmware-version` | sensor (diag) | `evse/software-version` | **renamed** |
| `status` | `operational-state` | sensor (enum) | `evse/status` | **renamed**: `status` → `operational-state` |
| `switch` | `lock-state` | binary_sensor | `evse/lock-state` | moved |
| `meter` | `advertised-current` | sensor (A) | `evse/advertised-current` | moved |
| `config` | `user-max-charge-current` | sensor (A, settable) | `evse/user-max-charge-current` | moved; new `config` capability; dynamic `$format=lower:max-charge-current` preserved |
| `config` | `max-charge-current` | sensor (A, diag) | `evse/max-charge-current` | moved |

#### Retired from `evse`

- `feed` — replaced by `connection/feeds-device-id` triplet on the feeding circuit

### Circuit child — `circuit` device (`<circuit-uuid>`)

This is the heaviest restructure — circuits are the most numerous device type, and the polarity flips on the boolean trio (`isNeverBackup`, `alwaysOn`, `isSheddable`) are user-visible.

| Capability | Property | HA platform | Old location | Notes |
|------------|----------|-------------|--------------|-------|
| `info` | `name` (settable) | (HA-side name source) | `circuit/name` | moved; settable preserved |
| `info` | `breaker-rating` | sensor (A, diag) | `circuit/breaker-rating` | moved |
| `info` | `tab-number` | sensor (diag) | `circuit/space` | **renamed**: `space` → `tab-number` |
| `info` | `dipole` | binary_sensor (diag) | `circuit/dipole` | moved; 1-pole circuits now publish `False` (was null) |
| `info` | `dedicated` / `tags` / `external-ids` | — | — | **omitted in v1** (registries not yet shipped) |
| `meter` | `current` | sensor (A) | `circuit/current` | moved |
| `meter` | `active-power` | sensor (W) | `circuit/active-power` | moved; firmware-bug unit override (declared kW, actual W) still required; **sign convention unchanged** — integration still negates so positive=consumption |
| `meter` | `imported-energy` / `exported-energy` | sensor (Wh, total_increasing) | `circuit/*` | moved; **monotonicity workaround still required** (see [AN-001](appnote-AN001-energy-counter-monotonicity.md)) |
| `switch` | `relay` | switch (settable, gated) | `circuit/relay` | moved; `$settable` gated on `priority/relay-controllable=true` at runtime |
| `switch` | `relay-requester` | sensor (enum) | `circuit/relay-requester` | moved; **`panel_enums.BranchRequester` realigned** (BACKUP→LOAD_SHED, NEVER_BACKUP→CONFIGURATION, ALWAYS_ON→CONFIGURATION, INVERTER→PCS, PCS_FAIL_SAFE→PCS; USER/FAULT/NONE/UNKNOWN unchanged). Two pairs of legacy values collapse — check select options. |
| `priority` | `shed-priority` (settable, gated) | select | `circuit/shed-priority` | moved; `$settable=false` when commissioned as permanent OFF_GRID, otherwise settable. Enum domain unchanged (`UNKNOWN`, `OFF_GRID`, `SOC_THRESHOLD`, `NEVER`). |
| `priority` | `pcs-managed` | binary_sensor (diag) | `circuit/pcs-managed` | moved |
| `priority` | `pcs-priority` | sensor (diag) | `circuit/pcs-priority` | moved |
| `priority` | `relay-controllable` | binary_sensor (diag) | `circuit/alwaysOn` (inverted) | **renamed + polarity-flipped**: `relay-controllable = !alwaysOn`. Now published per spec; also drives `$settable` on `circuit/switch/relay`. |
| `connection` | `feeds-device-id` / `feeds-device-type` / `feeds-device-status` / `count` | sensor (diag) | — | **net-new**; populated when this circuit is commissioned as feeding a specific DER (PV, IN_PANEL BESS, EVSE) |

#### Retired from `circuit`

- `isSheddable` → derivable in the consumer as `shed-priority != NEVER && relay-controllable`. Drop the entity entirely; if a UI element is wanted, build it as a template sensor in HA-land.
- `isNeverBackup` → polarity-flipped and demoted to *internal* on the panel side (`shed-priority-settable`); not published. Integration should not look for it.
- `alwaysOn` → polarity-flipped to `relay-controllable` (see above).

## Unique-ID scheme

```
{panel-serial}_{device-id}_{capability}_{property-id}
```

Examples:

```
nt-2024-a1b2c_nt-2024-a1b2c_info_serial-number              ← panel-root info/serial-number
nt-2024-a1b2c_nt-2024-a1b2c_meter_l1-voltage                ← panel-root meter/l1-voltage
nt-2024-a1b2c_nt-2024-a1b2c-lugs-up_meter_imported-energy   ← upstream-lugs energy
nt-2024-a1b2c_nt-2024-a1b2c-pw3-12345_soc_soc               ← BESS SOC
nt-2024-a1b2c_nt-2024-a1b2c-pw3-12345-mid_grid_islanding-state  ← MID islanding-state
nt-2024-a1b2c_a1b2c3d4-e5f6_meter_active-power              ← circuit power
nt-2024-a1b2c_a1b2c3d4-e5f6_priority_shed-priority          ← circuit shed-priority
```

The leading `{panel-serial}_` prefix is intentional even when `{device-id}` already contains the panel serial — it keeps unique-IDs globally distinct across multiple SPAN integrations in the same HA install (multi-home users, sub-panels, etc.) and matches the existing 0.1.x prefix pattern. Circuits use the bare UUID device-id (no panel-serial prefix in the Homie device-id) so the panel-serial prefix on the unique-ID is the *only* thing keeping their IDs unique across panels.

## HA device tree

| HA device | `identifiers` | `via_device` | Source |
|-----------|---------------|--------------|--------|
| Panel | `(DOMAIN, panel-serial)` | — (root) or panel-A from `link_subpanel` | `<panel-serial>` Homie device |
| Upstream lugs | `(DOMAIN, panel-serial-lugs-up)` | panel-serial | `<panel-serial>-lugs-up` |
| Downstream lugs | `(DOMAIN, panel-serial-lugs-dn)` | panel-serial | `<panel-serial>-lugs-dn` |
| BESS | `(DOMAIN, panel-serial-bess-serial)` | panel-serial | `<panel-serial>-<bess-serial>` |
| MID | `(DOMAIN, panel-serial-bess-serial-mid)` | panel-serial-bess-serial | `<panel-serial>-<bess-serial>-mid` |
| PV | `(DOMAIN, panel-serial-pv-id)` | panel-serial | `<panel-serial>-<pv-id>` |
| EVSE | `(DOMAIN, panel-serial-drive-serial)` | panel-serial | `<panel-serial>-<drive-serial>` |
| Circuit | `(DOMAIN, circuit-uuid)` | panel-serial | `<circuit-uuid>` |

`via_device` mirrors the Homie `$parent` for everything except the panel root, where the user-managed `link_subpanel` service is the only authoritative source (no `$parent` on a sub-panel root — see decision #4).

## node_mappers.py restructure

Current dispatch table (`node_mappers.py:771`) is keyed by `homie_device_type` string and maps each to a function taking `node_id` + props and producing `EntitySpec` lists. The new dispatch is keyed by `(homie_device_type, capability)` because capabilities are now first-class:

```python
CAPABILITY_MAPPERS: dict[tuple[str, str], CapabilityMapper] = {
    ("distribution-enclosure", "info"):           _map_enclosure_info,
    ("distribution-enclosure", "door"):           _map_enclosure_door,
    ("distribution-enclosure", "meter"):          _map_enclosure_meter,
    ("distribution-enclosure", "status"):         _map_enclosure_status,
    ("distribution-enclosure", "pcs"):            _map_enclosure_pcs,
    ("distribution-enclosure", "power-flows"):    _map_enclosure_power_flows,
    ("distribution-enclosure", "shed-forecast"):  _map_enclosure_shed_forecast,
    ("distribution-enclosure", "shed"):           _map_enclosure_shed,
    ("lugs", "info"):                             _map_lugs_info,
    ("lugs", "meter"):                            _map_lugs_meter,
    ("lugs", "connection"):                       _map_lugs_connection,
    ("bess", "info"):                             _map_bess_info,
    ("bess", "soc"):                              _map_bess_soc,
    ("mid", "info"):                              _map_mid_info,
    ("mid", "grid"):                              _map_mid_grid,
    ("pv", "info"):                               _map_pv_info,
    ("evse", "info"):                             _map_evse_info,
    ("evse", "status"):                           _map_evse_status,
    ("evse", "switch"):                           _map_evse_switch,
    ("evse", "meter"):                            _map_evse_meter,
    ("evse", "config"):                           _map_evse_config,
    ("circuit", "info"):                          _map_circuit_info,
    ("circuit", "meter"):                         _map_circuit_meter,
    ("circuit", "switch"):                        _map_circuit_switch,
    ("circuit", "priority"):                      _map_circuit_priority,
    ("circuit", "connection"):                    _map_circuit_connection,
}
```

The `entities_from_description` entry point walks each descendant device's capabilities and dispatches per `(device.homie_device_type, capability_name)`. Capabilities not in the table are logged at INFO and skipped — forward-compatible with spec additions.

Settable-gated properties (`circuit/switch/relay`, `circuit/priority/shed-priority`) read their `$settable` from the property value at construction time; the entity surfaces as a non-settable sensor if the gate is closed at HA startup. (Live re-gating mid-runtime is a future enhancement.)

## Net-new entity surface (user-visible)

Several capabilities are entirely new and surface as new HA entities the user has never seen:

- **Panel root**: `shed-forecast/{total-time-remaining, time-to-priority-shed, full-charge-total-time-remaining, full-charge-time-to-priority-shed, confidence}` — five new sensors for the SPAN BTR forecast. Particularly valuable for an Energy Dashboard "time remaining" widget.
- **Panel root**: `shed/{override, soc-threshold}` — a settable switch (override) and a diagnostic sensor (soc-threshold). Replaces the settable half of the retired `core/dominant-power-source`.
- **MID grandchild per BESS**: full `info` + `grid` capabilities. `grid/islanding-state` carries the value of the old `bess/grid-state`, but `grid-state` and `grid-forming-entity` are entirely new diagnostic sensors.
- **Connection capability on circuits and upstream-lugs**: `feeds-device-id` / `feeds-device-type` / `feeds-device-status` triplet (plus `count` for aggregated DERs). The `feeds-device-status` enum (`OK`/`LOST`/`DEGRADED`) replaces the retired `bess/connected` boolean in the IN_PANEL case.

These should be additive — no replacement migration logic needed (per decision #2).

## Retired entity surface (user-visible deletions)

- **Panel**: `dominant-power-source` (split into MID grandchild + shed/override; no direct replacement entity)
- **BESS**: `relative-position`, `feed`, `connected` (derivable from connection records; if a UI element is wanted, build it as a template sensor in HA-land)
- **PV**: `relative-position`, `feed` (same as BESS)
- **EVSE**: `feed` (same)
- **Circuit**: `isSheddable`, `isNeverBackup`, `alwaysOn` (replaced by `relay-controllable` + derivable rules)

## Reference snapshots

Tree-v1 JSON captures of dcj's home panels are at `~/projects/span.io/shadow-repo/device/gateway/services/ebus-panel-adapter/scripts/`:

- `nt-0000-abc12-after-PR6-deploy-tree.json` — panel after migration deploy
- `nt-0000-def34-after-PR6-deploy-tree.json` — sub-panel after migration deploy
- `*-before-PR6-deploy.json` files exist for panel-a, panel-c, panel-b — flat-publication captures for diff'ing against tree captures

These are the test fixtures for the rewrite. `snapshot-tool conformance` (spanio/device PR #4229) can validate a fresh capture against PropertyDefinition expectations.

## Test plan

- **Unit**: rewire `tests/` fixtures to the tree-v1 snapshot JSONs above. The existing `test_node_mappers.py` shape (one test per mapper) carries over with new mapper names; new tests are needed for capabilities that didn't exist before (`shed-forecast`, `shed`, `connection`, MID).
- **Integration**: replay snapshot-driven discovery via `ebus_sdk.Controller` (offline broker — same pattern as 0.1.x tests) and assert the entity-spec output matches expectations per device-class × capability.
- **Live**: install on HA Yellow against panel-a (and the two sub-panels panel-c / panel-b) once they're running the OTA'd `r202627` firmware. Verify:
  - HA device tree matches the table above
  - Settable entities (`circuit/switch/relay`, `circuit/priority/shed-priority`, `evse/config/user-max-charge-current`, `shed/override`) actually write through
  - Energy Dashboard "Now" power Sankey still works after the IDs change
  - `link_subpanel` still wires panel-c and panel-b under panel-a for Sankey hierarchy

## Out of scope (deferred / future)

- **Conformance gating**: auto-warning the user when their panel publishes properties the integration doesn't know about. Possible follow-up via `snapshot-tool conformance` integration. Not v1.
- **Live re-gating** of `$settable`. v1 reads `$settable` at entity construction; if `priority/relay-controllable` flips at runtime, the corresponding `switch/relay` entity stays in its construction-time settability. Re-gating on property-change is a future enhancement.
- **Spec direction-2 connection capability** (PV/BESS/EVSE gaining `connection/fed-by-*` on themselves, not just the enclosure side). Tracked spec-side; if it lands, v0.2.x can adopt for nicer "what feeds this PV?" navigation without breaking v1.
- **PV `meter` capability**. Spec defines `pv/meter/active-power` + `pv/meter/exported-energy` but SPAN omits in v1 — see PV section above. Generation Power virtual entity re-derived from the feeding circuit's meter remains the v1 answer.
- **Panel-integrated MID**. Out of scope for `SpanG2Panel` (no integrated MID on MAIN_32). Future `SpanG3Panel` concern.

## Resolved decisions (formerly open questions)

- **Multi-panel scoping under the tree model.** *Resolved via SDK-o1h, shipped in ebus-sdk 0.3.0.* The integration switches from today's `device_id=<panel-serial>` to `root_device_id=<panel-serial>`. The SDK auto-subscribes to descendants via parent `$description.children` reconcile, gated on the parent's `$state` init→ready edge per Homie 5 semantics — a mid-transition `$description` is stashed but not acted upon. Each `Controller` still sees exactly one panel's tree on a shared broker; the per-panel scope-isolation property is preserved.
- **Effective-state propagation in HA `available`.** *Resolved: propagate via the Homie 5 effective-state rule.* HA-side `available` rides `Controller.get_effective_state(device_id)`. When the panel root is `init`/`disconnected`/`lost`/`sleeping`, every descendant's `available` goes false simultaneously without each descendant having to republish its own state — matches the spec (`HOMIE_EFFECTIVE_STATE_TABLE` at `homie.py:225`) and is the right UX (a flapping or rebooting panel shouldn't show half its circuits as available).
- **Memory diagnostics retention.** *Resolved: re-baseline during the post-cut soak, not before.* The 30-minute `Memory diagnostics: peak_rss=...` log line stays as-is. Tree-walking adds entities (~30–40% more per panel from net-new `shed-forecast`, `shed`, MID, and `connection` capabilities) and more `DiscoveredDevice` objects (~36 per panel vs 1 in the flat model), so steady-state RSS will be higher. After deploying 0.2.0 on panel-a/panel-c/panel-b, let it soak 48h, capture the new peak RSS and tracemalloc top-5, and stash the numbers somewhere reachable for "is it leaking?" investigations later. (Historical note: the SPAN-ibx investigation in early 2026 ultimately attributed the HA Yellow freeze to an unrelated broken kafka integration buffering messages to an unreachable cluster, not to span_ebus itself — the diagnostics are useful operational telemetry but don't carry a memory-leak burden of proof from that incident.)
