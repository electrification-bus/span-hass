# Energy Counter Decrease Suppression

## Background

SPAN firmware occasionally reports energy counter values that decrease by small amounts (typically 0.1 Wh). This has been observed on the downstream lug `imported-energy` and `exported-energy` counters across multiple panels, occurring approximately every 30 seconds during normal operation.

A larger recalibration event was also observed: a ~5.4% decrease on panel `nt-xxxx-xxxxx` after 14 days of continuous uptime, suggesting the firmware performs periodic counter recalibration while running.

## Problem

Home Assistant's `total_increasing` state class assumes energy counters are monotonically non-decreasing. When a decrease is detected, HA's recorder treats it as a **meter reset** (analogous to a utility meter rollover) and adds the full previous accumulated value to the running total. A 0.1 Wh drop on a 12.5 MWh counter produces a false +12.5 MWh spike in long-term statistics, corrupting energy dashboard data.

## Solution

In `sensor.py`, the `_update_from_value()` method for `TOTAL_INCREASING` sensors implements a high-water-mark hold:

1. When a new value is **less than** the current value, the update is suppressed and the previous value is retained.
2. A `WARNING` log is emitted on the first suppression event for each entity (subsequent suppressions within the same hold period are silent).
3. Once the counter **catches back up** to or exceeds the high-water mark, normal tracking resumes and an `INFO` log confirms recovery.

This approach:
- Prevents false meter-reset spikes in HA statistics
- Preserves accurate energy accounting (the suppressed delta is typically < 1 Wh and is recovered within seconds)
- Logs enough detail for monitoring without flooding the log

## Observed Frequency

In an 8-hour observation window (2026-02-24/25), 972 suppression events were recorded across two panels (`nt-xxxx-xxxxx` and `nt-yyyy-yyyyy`), all on downstream lug energy counters with delta of 0.1 Wh. Each suppression was followed by a catch-up within 1-2 seconds.

## Related

- See [AN-001: SPAN Energy Counter Monotonicity](appnote-AN001-energy-counter-monotonicity.md) for guidance to other SPAN API developers.
- Commit: `c110ef6 Suppress energy counter decreases in total_increasing sensors`
