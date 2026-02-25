# AN-001: SPAN Energy Counter Monotonicity

> **Status:** Active — workaround required in client applications
> **Date:** 2026-02-25
> **Affects:** All SPAN panels observed (firmware spanos2/r202603/05)
> **Properties:** `imported-energy`, `exported-energy` on circuit and lug nodes

## Summary

SPAN panel energy counters (`imported-energy`, `exported-energy`) are not guaranteed to be monotonically non-decreasing. The Homie MQTT API may report values that temporarily decrease, requiring client applications to handle this defensively.

## Observed Behavior

Two patterns of counter decrease have been observed:

### Small Decreases (~0.1 Wh)

During normal operation, energy counters may drop by approximately 0.1 Wh and recover within 1-2 seconds. This occurs frequently (observed ~120 events per hour per panel) on downstream lug counters. The pattern suggests floating-point accumulation artifacts or sub-second sampling jitter in the firmware's energy integration.

### Large Recalibration (~5%)

After extended uptime (~14 days), a single large decrease of approximately 5.4% was observed on one panel's downstream lug counter. This suggests the firmware performs periodic recalibration of its energy accumulators against a reference, and the recalibrated value may be lower than the running total.

## Impact on Clients

Any client that treats these properties as monotonically increasing counters will produce incorrect results:

- **Home Assistant** (`total_increasing` state class): Interprets a decrease as a meter reset, adding the full previous value to long-term statistics. A 0.1 Wh drop on a 12 MWh counter creates a false +12 MWh spike.

- **Custom dashboards / analytics**: Calculating energy deltas between readings (E2 - E1) will produce negative values during decreases, which may be misinterpreted or cause underflow errors.

## Recommended Workaround

Client applications should implement a **high-water-mark hold** for energy counter properties:

```
on_new_value(property, new_value):
    if property.state_class == TOTAL_INCREASING:
        if previous_value is not None and new_value < previous_value:
            # Suppress the decrease; retain previous value
            log_warning_once("Counter decrease: %s -> %s", previous_value, new_value)
            return
        if suppressing and new_value >= high_water_mark:
            # Counter has caught up; resume normal tracking
            log_info("Counter caught up at %s", new_value)
            suppressing = false
    previous_value = new_value
```

This approach:
- Prevents false spikes from small decreases
- Handles large recalibration events gracefully (value is frozen until the counter naturally exceeds the high-water mark)
- Preserves long-term accuracy since the counter always catches back up

## Properties Affected

| Node Type | Property | Observed |
|-----------|----------|----------|
| `energy.ebus.device.lugs.upstream` | `imported-energy` | Yes |
| `energy.ebus.device.lugs.upstream` | `exported-energy` | Yes |
| `energy.ebus.device.lugs.downstream` | `imported-energy` | Yes |
| `energy.ebus.device.lugs.downstream` | `exported-energy` | Yes |
| `energy.ebus.device.circuit` | `imported-energy` | Likely |
| `energy.ebus.device.circuit` | `exported-energy` | Likely |

Circuit-level counters are expected to exhibit the same behavior but have not been independently verified at the time of writing.

## Expectation for SPAN API

Ideally, the SPAN Homie API would guarantee monotonically non-decreasing values for energy counter properties. If the firmware performs internal recalibration, the published MQTT values should be adjusted to ensure the externally visible counter never decreases.

If a future SPAN firmware release guarantees monotonic energy counters, this app note will be updated to reflect the change. The client-side workaround remains recommended as defensive coding regardless.

## References

- [span-hass implementation](../custom_components/span_ebus/sensor.py) — `_update_from_value()` in `SpanEbusSensor`
- [Internal notes](energy-counter-suppression.md) — observation data and implementation details
- [Feature request to SPAN](https://github.com/spanio/SPAN-API-Client-Docs/discussions/5) — request for firmware-level monotonicity guarantee
