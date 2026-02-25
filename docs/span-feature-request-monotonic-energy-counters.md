# Feature Request: Guarantee monotonically non-decreasing energy counters

> For submission as a Discussion on the SPAN-API-Client-Docs repo.

---

**Title:** Guarantee monotonically non-decreasing energy counters (imported-energy, exported-energy)

**Category:** Feature Request

---

### Summary

Energy counter properties (`imported-energy`, `exported-energy`) on circuit and lug nodes occasionally report values that decrease. This breaks the implicit contract that cumulative energy counters are monotonically non-decreasing, and causes significant issues for client applications that track energy accumulation over time.

### Observed Behavior

Two patterns of counter decrease have been observed on panels running `spanos2/r202603/05` firmware:

**1. Frequent small decreases (~0.1 Wh)**
During normal operation, energy counters drop by approximately 0.1 Wh and recover within 1-2 seconds. This occurs roughly 120 times per hour per panel on downstream lug counters (`imported-energy`, `exported-energy`).

**2. Infrequent large decreases (~5%)**
After approximately 14 days of continuous uptime, a single large decrease of ~5.4% was observed on a downstream lug `imported-energy` counter, suggesting periodic internal recalibration.

### Impact on Clients

Any client that treats these properties as cumulative counters will produce incorrect results when a decrease occurs. For example:

- **Home Assistant** uses the `total_increasing` state class for energy counters. When a decrease is detected, HA interprets it as a meter reset (analogous to a utility meter rolling over) and adds the full previous accumulated value to long-term statistics. A 0.1 Wh decrease on a 12 MWh counter creates a false **+12 MWh spike** in recorded statistics, corrupting energy dashboard data.

- **Custom analytics** calculating energy deltas between consecutive readings (E2 - E1) will produce negative values, which may be silently dropped, misinterpreted, or cause errors.

Both patterns require client-side workarounds (e.g., high-water-mark suppression), which adds complexity to every client implementation.

### Request

Please guarantee that the published MQTT values for `imported-energy` and `exported-energy` are **monotonically non-decreasing** on all node types:

- `energy.ebus.device.circuit`
- `energy.ebus.device.lugs.upstream`
- `energy.ebus.device.lugs.downstream`

If the firmware performs internal recalibration of energy accumulators, the externally published values should be adjusted so the counter never decreases from the client's perspective.

### Affected Properties

| Node Type | Properties |
|-----------|-----------|
| `energy.ebus.device.circuit` | `imported-energy`, `exported-energy` |
| `energy.ebus.device.lugs.upstream` | `imported-energy`, `exported-energy` |
| `energy.ebus.device.lugs.downstream` | `imported-energy`, `exported-energy` |

### Workaround

We have implemented a client-side high-water-mark hold in our Home Assistant integration ([span-hass](https://github.com/electrification-bus/span-hass)). When a counter decrease is detected, the previous value is retained until the counter catches back up. This prevents false spikes but should not be necessary if the API guarantees monotonicity.

Details are documented in [AN-001: SPAN Energy Counter Monotonicity](https://github.com/electrification-bus/span-hass/blob/main/docs/appnote-AN001-energy-counter-monotonicity.md).
