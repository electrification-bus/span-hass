# Energy Counter Decrease Suppression

## Background

SPAN firmware occasionally reports energy counter values that decrease by small amounts (typically 0.1 Wh). This has been observed on the downstream lug `imported-energy` and `exported-energy` counters across multiple panels, occurring approximately every 30 seconds during normal operation.

A larger recalibration event was also observed: a ~5.4% decrease on panel `nt-xxxx-xxxxx` after 14 days of continuous uptime, suggesting the firmware performs periodic counter recalibration while running.

## Problem

Home Assistant's `total_increasing` state class assumes energy counters are monotonically non-decreasing. When a decrease is detected, HA's recorder treats it as a **meter reset** (analogous to a utility meter rollover) and adds the full previous accumulated value to the running total. A 0.1 Wh drop on a 12.5 MWh counter produces a false +12.5 MWh spike in long-term statistics, corrupting energy dashboard data.

## Solution

In `sensor.py`, the `_update_from_value()` method for `TOTAL_INCREASING` sensors implements a high-water-mark hold:

1. When a new value is **less than** the current value, the update is suppressed and the previous value is retained. This happens for **every** decrease regardless of size, because Home Assistant reads any decrease at all as a meter reset.
2. The first suppression of each hold period is logged. Its level depends on the size of the decrease, measured against the `COUNTER_DECREASE_DEADBAND_WH` threshold in `const.py` (1 Wh, converted to the sensor's own unit so a counter published in kWh is not given a deadband a thousand times too permissive). A decrease **above** the deadband is a recalibration and logs at `WARNING`; a decrease **at or below** it is routine publisher jitter and logs at `DEBUG`.
3. Once the counter **catches back up** to or exceeds the high-water mark, normal tracking resumes. The recovery notice is logged at the volume matching the notice that opened the episode: `INFO` after a `WARNING`, `DEBUG` after a `DEBUG`, so a sub-deadband blip is silent from end to end.

This approach:

- Prevents false meter-reset spikes in HA statistics
- Preserves accurate energy accounting (the suppressed delta is typically < 1 Wh and is recovered within seconds)
- Keeps the routine jitter out of the log while leaving the events that matter loud

### Why the deadband exists

The original implementation logged every hold at `WARNING` and every recovery at `INFO`, irrespective of magnitude. Because the jitter recurs every one to two seconds on the affected counters, a multi-panel install wrote a `WARNING` and an `INFO` line into the log continuously and indefinitely. Measured on a three-panel install in September 2026, 11 of the last 100 log lines were this one message pair, on `c1akc_downstream_lugs_exported_energy` and `c192x_lugs_imported_energy_2`, all with a delta of 0.1 Wh.

The two cases are separated by five to seven orders of magnitude, which is what makes a fixed threshold safe. The jitter is 0.1 Wh, one unit in the last place at the published precision, on counters in the tens of MWh: floating-point noise rather than an energy event. The recalibrations this guard was written for are far larger; the ones observed on the PV energy counter were 115 kWh, 153 kWh, 573 kWh and 1.01 MWh.

Note that the deadband changes **only the log level**. The suppression itself is unconditional, so the protection against false meter-reset spikes is exactly as strong as before.

## Observed Frequency

In an 8-hour observation window (2026-02-24/25), 972 suppression events were recorded across two panels (`nt-xxxx-xxxxx` and `nt-yyyy-yyyyy`), all on downstream lug energy counters with delta of 0.1 Wh. Each suppression was followed by a catch-up within 1-2 seconds.

## Related

- See [AN-001: SPAN Energy Counter Monotonicity](appnote-AN001-energy-counter-monotonicity.md) for guidance to other SPAN API developers.
- Commit: `c110ef6 Suppress energy counter decreases in total_increasing sensors`
