"""Map Homie 5 parent/child tree → Home Assistant entity descriptors.

This module is the G3P-23496 successor to the flat-publication ``node_mappers``.
Phase 1 lands the dispatch infrastructure plus two example mappers
(``(distribution-enclosure, info)`` and ``(distribution-enclosure, door)``); the
remaining 24 mappers are stubbed and will be filled in during Phase 2 of
SPAN-d5i. See ``docs/g3p-23496-migration.md`` and the canonical property-mapping
CSV at ``~/projects/span.io/shadow-repo/device/doc/g3p-23496-property-mapping.csv``
for the per-capability mapping table.

The dispatcher walks a tree of Homie devices (the same shape that
``ebus_sdk.Controller`` exposes when constructed with ``root_device_id=``, and
the same shape that the tree-v1 snapshot JSONs in ``tests/fixtures/tree/`` use)
and routes each capability node through a per-(device-class, capability)
function.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import logging
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    Platform,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTime,
)

from .const import (
    CAPABILITY_CONFIG,
    CAPABILITY_CONNECTION,
    CAPABILITY_DOOR,
    CAPABILITY_GRID,
    CAPABILITY_INFO,
    CAPABILITY_METER,
    CAPABILITY_PCS,
    CAPABILITY_POWER_FLOWS,
    CAPABILITY_PRIORITY,
    CAPABILITY_SHED,
    CAPABILITY_SHED_FORECAST,
    CAPABILITY_SOC,
    CAPABILITY_STATUS,
    CAPABILITY_SWITCH,
    DEVICE_TYPE_BESS,
    DEVICE_TYPE_CIRCUIT,
    DEVICE_TYPE_DISTRIBUTION_ENCLOSURE,
    DEVICE_TYPE_EVSE,
    DEVICE_TYPE_LUGS,
    DEVICE_TYPE_MID,
    DEVICE_TYPE_PV,
    HOMIE_DEVICE_TYPE_PREFIX,
)

_LOGGER = logging.getLogger(__name__)


# ── EntitySpec ────────────────────────────────────────────────────────────


@dataclass
class EntitySpec:
    """Descriptor for an entity to be created from a Homie capability property.

    The (device_id, capability, property_id) triplet locates the property
    uniquely on the wire and forms the basis of the entity's HA ``unique_id``.
    """

    # Tree position — locates the property on the wire.
    device_id: str
    capability: str
    property_id: str

    # HA entity descriptor.
    platform: Platform
    name: str
    device_class: Any | None = None
    state_class: SensorStateClass | None = None
    native_unit: str | None = None
    entity_category: EntityCategory | None = None
    options: list[str] = field(default_factory=list)
    icon: str | None = None
    settable: bool = False
    on_values: set[str] = field(default_factory=set)

    # Subscribe to a different property than property_id for MQTT updates.
    # When set, unique_id uses property_id but the MQTT callback registers on
    # source_property_id. Empty string = "subscribe to property_id itself".
    source_property_id: str = ""

    # Value transform: negate numeric values (used for circuit active-power so
    # consumption is positive in HA, matching the Energy Dashboard convention).
    negate: bool = False

    # HA DeviceInfo construction. device_type is the short device-class name
    # (one of DEVICE_TYPE_* constants in const.py); via_device_id is the
    # parent's Homie device-id for HA's via_device link (empty for root).
    device_type: str = ""
    device_name: str = ""
    via_device_id: str = ""


# ── Capability mappers ────────────────────────────────────────────────────

# Mapper signature: takes the device-id this capability node belongs to, the
# capability node-id, the parsed property declarations (the ``properties`` dict
# from the Homie description), and the full device data record (for
# cross-property lookups like vendor-name when building names). Returns zero or
# more EntitySpecs.
Mapper = Callable[[str, str, dict[str, Any], dict[str, Any]], list[EntitySpec]]


# ─ Panel-root (distribution-enclosure) capabilities ─


def _map_enclosure_info(
    device_id: str,
    capability: str,
    properties: dict[str, Any],
    device_data: dict[str, Any],
) -> list[EntitySpec]:
    """Panel identity: vendor, model, serials, versions, data-model discriminator."""
    declared: dict[str, dict[str, Any]] = {
        "vendor-name": {"name": "Vendor"},
        "model": {"name": "Model"},
        "serial-number": {"name": "Serial Number"},
        "hardware-version": {"name": "Hardware Version"},
        # Spec renamed software-version → firmware-version in tree-v1; some
        # snapshots predate the rename. Mapping both keeps Phase 1 robust to
        # in-flight firmware; once the firmware-side rename ships everywhere,
        # the software-version row drops out and the entity stops appearing.
        "firmware-version": {"name": "Firmware Version"},
        "software-version": {"name": "Firmware Version"},
        "data-model-version": {"name": "eBus Data-Model Version"},
    }
    specs: list[EntitySpec] = []
    for prop_id, meta in declared.items():
        if prop_id not in properties:
            continue
        specs.append(
            EntitySpec(
                device_id=device_id,
                capability=capability,
                property_id=prop_id,
                platform=Platform.SENSOR,
                name=meta["name"],
                entity_category=EntityCategory.DIAGNOSTIC,
            )
        )
    return specs


def _map_enclosure_door(
    device_id: str,
    capability: str,
    properties: dict[str, Any],
    device_data: dict[str, Any],
) -> list[EntitySpec]:
    """Panel door open/closed as a tamper-class binary sensor.

    Spec renamed door/door → door/state alongside the move into the door
    capability; some firmware snapshots still publish the legacy id. Prefer the
    new name, fall back to the legacy.
    """
    if "state" in properties:
        prop_id = "state"
    elif "door" in properties:
        prop_id = "door"
    else:
        return []
    return [
        EntitySpec(
            device_id=device_id,
            capability=capability,
            property_id=prop_id,
            platform=Platform.BINARY_SENSOR,
            name="Door",
            device_class=BinarySensorDeviceClass.TAMPER,
            on_values={"OPEN"},
        )
    ]


def _emit_from_table(
    device_id: str,
    capability: str,
    properties: dict[str, Any],
    table: dict[str, dict[str, Any]],
) -> list[EntitySpec]:
    """Emit one EntitySpec per declared property that has a row in ``table``.

    Each row's dict is passed as kwargs to EntitySpec. Properties not in the
    description are skipped (forward-compatible with publishers that ship a
    subset of the spec).
    """
    specs: list[EntitySpec] = []
    for prop_id, meta in table.items():
        if prop_id not in properties:
            continue
        specs.append(
            EntitySpec(
                device_id=device_id,
                capability=capability,
                property_id=prop_id,
                **meta,
            )
        )
    return specs


def _map_enclosure_meter(
    device_id: str,
    capability: str,
    properties: dict[str, Any],
    device_data: dict[str, Any],
) -> list[EntitySpec]:
    """Panel-level voltages.

    The spec also lists mirrored l1/l2 currents, active-power, and imported/
    exported energies on this capability for parity with lugs, but SPAN flags
    them ``internal-only`` and does not publish them — those entities already
    exist on the upstream-lugs child device. The mapper does not emit
    duplicates even if a future firmware decides to publish the mirrors.
    """
    table: dict[str, dict[str, Any]] = {
        "l1-voltage": {
            "platform": Platform.SENSOR,
            "name": "L1 Voltage",
            "device_class": SensorDeviceClass.VOLTAGE,
            "state_class": SensorStateClass.MEASUREMENT,
            "native_unit": UnitOfElectricPotential.VOLT,
        },
        "l2-voltage": {
            "platform": Platform.SENSOR,
            "name": "L2 Voltage",
            "device_class": SensorDeviceClass.VOLTAGE,
            "state_class": SensorStateClass.MEASUREMENT,
            "native_unit": UnitOfElectricPotential.VOLT,
        },
    }
    return _emit_from_table(device_id, capability, properties, table)


def _map_enclosure_status(
    device_id: str,
    capability: str,
    properties: dict[str, Any],
    device_data: dict[str, Any],
) -> list[EntitySpec]:
    """Network connectivity, cloud connection, main relay, location metadata."""
    table: dict[str, dict[str, Any]] = {
        # Main relay (was core/relay): enum UNKNOWN/OPEN/CLOSED; surfaces as a
        # binary sensor where ``CLOSED`` means "on" (relay engaged).
        "relay": {
            "platform": Platform.BINARY_SENSOR,
            "name": "Main Relay",
            "icon": "mdi:electric-switch",
            "on_values": {"CLOSED"},
        },
        "ethernet": {
            "platform": Platform.BINARY_SENSOR,
            "name": "Ethernet",
            "device_class": BinarySensorDeviceClass.CONNECTIVITY,
            "entity_category": EntityCategory.DIAGNOSTIC,
        },
        "wifi": {
            "platform": Platform.BINARY_SENSOR,
            "name": "Wi-Fi",
            "device_class": BinarySensorDeviceClass.CONNECTIVITY,
            "entity_category": EntityCategory.DIAGNOSTIC,
        },
        "wifi-ssid": {
            "platform": Platform.SENSOR,
            "name": "Wi-Fi SSID",
            "entity_category": EntityCategory.DIAGNOSTIC,
        },
        # Spec renames vendor-cloud → cloud-connection alongside the move into
        # the status capability; legacy snapshots still publish the old id.
        # Declaring both with identical metadata means whichever the firmware
        # publishes, we surface one "Cloud Connection" sensor under the same
        # name (different unique_ids, but the firmware never publishes both).
        "cloud-connection": {
            "platform": Platform.SENSOR,
            "name": "Cloud Connection",
            "entity_category": EntityCategory.DIAGNOSTIC,
        },
        "vendor-cloud": {
            "platform": Platform.SENSOR,
            "name": "Cloud Connection",
            "entity_category": EntityCategory.DIAGNOSTIC,
        },
        "postal-code": {
            "platform": Platform.SENSOR,
            "name": "Postal Code",
            "entity_category": EntityCategory.DIAGNOSTIC,
        },
        "time-zone": {
            "platform": Platform.SENSOR,
            "name": "Time Zone",
            "entity_category": EntityCategory.DIAGNOSTIC,
        },
    }
    return _emit_from_table(device_id, capability, properties, table)


def _map_enclosure_pcs(
    device_id: str,
    capability: str,
    properties: dict[str, Any],
    device_data: dict[str, Any],
) -> list[EntitySpec]:
    """Power Control System capability.

    Carries the 5 SPAN current-limit families (import, feed-import, grid-import,
    off-grid-import, requested-import) plus their enablement enums and active
    booleans, plus ``enabled``/``active`` master flags, plus the relocated
    ``grid-islandable`` and ``breaker-rating`` from the old core node.
    """
    limit_kinds = (
        "import-limit",
        "feed-import-limit",
        "grid-import-limit",
        "off-grid-import-limit",
        "requested-import-limit",
    )
    table: dict[str, dict[str, Any]] = {
        "enabled": {
            "platform": Platform.BINARY_SENSOR,
            "name": "PCS Enabled",
            "entity_category": EntityCategory.DIAGNOSTIC,
        },
        "active": {
            "platform": Platform.BINARY_SENSOR,
            "name": "PCS Active",
            "entity_category": EntityCategory.DIAGNOSTIC,
        },
        "grid-islandable": {
            "platform": Platform.BINARY_SENSOR,
            "name": "Grid Islandable",
            "entity_category": EntityCategory.DIAGNOSTIC,
        },
        "breaker-rating": {
            "platform": Platform.SENSOR,
            "name": "Main Breaker Rating",
            "device_class": SensorDeviceClass.CURRENT,
            "native_unit": UnitOfElectricCurrent.AMPERE,
            "entity_category": EntityCategory.DIAGNOSTIC,
        },
    }
    for limit in limit_kinds:
        pretty = limit.replace("-", " ").title()
        table[limit] = {
            "platform": Platform.SENSOR,
            "name": pretty,
            "device_class": SensorDeviceClass.CURRENT,
            "state_class": SensorStateClass.MEASUREMENT,
            "native_unit": UnitOfElectricCurrent.AMPERE,
        }
        table[f"{limit}-enablement"] = {
            "platform": Platform.SENSOR,
            "name": f"{pretty} Enablement",
            "entity_category": EntityCategory.DIAGNOSTIC,
        }
        table[f"{limit}-active"] = {
            "platform": Platform.BINARY_SENSOR,
            "name": f"{pretty} Active",
            "entity_category": EntityCategory.DIAGNOSTIC,
        }
    return _emit_from_table(device_id, capability, properties, table)


def _map_enclosure_power_flows(
    device_id: str,
    capability: str,
    properties: dict[str, Any],
    device_data: dict[str, Any],
) -> list[EntitySpec]:
    """Panel-level directional power totals (pv / battery / grid / site)."""
    labels = {"pv": "PV", "battery": "Battery", "grid": "Grid", "site": "Site"}
    table: dict[str, dict[str, Any]] = {
        prop_id: {
            "platform": Platform.SENSOR,
            "name": f"{label} Power",
            "device_class": SensorDeviceClass.POWER,
            "state_class": SensorStateClass.MEASUREMENT,
            "native_unit": UnitOfPower.WATT,
        }
        for prop_id, label in labels.items()
    }
    return _emit_from_table(device_id, capability, properties, table)


def _map_enclosure_shed_forecast(
    device_id: str,
    capability: str,
    properties: dict[str, Any],
    device_data: dict[str, Any],
) -> list[EntitySpec]:
    """SPAN BTR forecast — net-new capability, present only when ≥1 BESS commissioned.

    Four duration sensors (current load + full-charge variants of each) plus a
    confidence enum sensor.
    """
    time_props = {
        "total-time-remaining": "Battery Time Remaining",
        "time-to-priority-shed": "Time to Priority Shed",
        "full-charge-total-time-remaining": "Battery Time Remaining at Full Charge",
        "full-charge-time-to-priority-shed": "Time to Priority Shed at Full Charge",
    }
    table: dict[str, dict[str, Any]] = {
        prop_id: {
            "platform": Platform.SENSOR,
            "name": label,
            "device_class": SensorDeviceClass.DURATION,
            "state_class": SensorStateClass.MEASUREMENT,
            "native_unit": UnitOfTime.MINUTES,
        }
        for prop_id, label in time_props.items()
    }
    table["confidence"] = {
        "platform": Platform.SENSOR,
        "name": "Shed Forecast Confidence",
        "entity_category": EntityCategory.DIAGNOSTIC,
    }
    return _emit_from_table(device_id, capability, properties, table)


def _map_enclosure_shed(
    device_id: str,
    capability: str,
    properties: dict[str, Any],
    device_data: dict[str, Any],
) -> list[EntitySpec]:
    """SPAN shed control — net-new capability, present only when ≥1 BESS commissioned.

    ``override`` is a settable boolean that replaces the settable half of the
    retired ``core/dominant-power-source``; the firmware silently ignores
    out-of-condition writes (accepts true only when islanding-state=OFF_GRID
    AND the BESS comm is LOST/DEGRADED; accepts false always).
    """
    table: dict[str, dict[str, Any]] = {
        "override": {
            "platform": Platform.SWITCH,
            "name": "Shed Override",
            "icon": "mdi:flash-off",
            "settable": True,
        },
        "soc-threshold": {
            "platform": Platform.SENSOR,
            "name": "Shed SOC Threshold",
            "device_class": SensorDeviceClass.BATTERY,
            "native_unit": PERCENTAGE,
            "entity_category": EntityCategory.DIAGNOSTIC,
        },
    }
    return _emit_from_table(device_id, capability, properties, table)


# ─ Lugs (upstream + downstream) capabilities ─


def _lug_direction(device_data: dict[str, Any]) -> str:
    """Return the lug's direction ('upstream'/'downstream'/'') from runtime properties.

    The publisher reports lowercase even though the description enum format is
    UPPERCASE — normalise before comparing. Returns the empty string when the
    direction isn't yet known (e.g. property values haven't arrived at the time
    the descriptor is constructed); callers should treat that as "unknown".
    """
    raw = device_data.get("properties", {}).get("info/direction", "")
    return str(raw).lower()


def _map_lugs_info(
    device_id: str,
    capability: str,
    properties: dict[str, Any],
    device_data: dict[str, Any],
) -> list[EntitySpec]:
    """Lugs info: direction (upstream / downstream) as a diagnostic sensor.

    The direction is a static device property — the device-info name typically
    already encodes it ("Upstream Lugs"), but exposing it as a diagnostic
    entity gives automations a queryable handle.
    """
    if "direction" not in properties:
        return []
    return [
        EntitySpec(
            device_id=device_id,
            capability=capability,
            property_id="direction",
            platform=Platform.SENSOR,
            name="Direction",
            entity_category=EntityCategory.DIAGNOSTIC,
        )
    ]


def _map_lugs_meter(
    device_id: str,
    capability: str,
    properties: dict[str, Any],
    device_data: dict[str, Any],
) -> list[EntitySpec]:
    """Lugs meter: per-leg currents, active-power, imported / exported energy.

    Energy entity names depend on direction. On the upstream lug, the dominant
    flow is grid → panel → loads, so ``imported-energy`` (= panel consumption
    from grid) carries the user-friendly name "Energy" and ``exported-energy``
    (= panel → grid export) is "Energy Returned" — matching the README. On the
    downstream lug, both flow directions reverse semantically; SPAN does not
    populate downstream values today (see the spec note about inter-panel
    feedthrough), so we use literal "Imported Energy" / "Exported Energy"
    names that will read unambiguously when a future firmware enables them.
    Power is direction-agnostic — positive always means "flowing into the
    panel," negative "flowing out" — so it stays "Power" in both directions.
    """
    is_upstream = _lug_direction(device_data) == "upstream"
    imported_name = "Energy" if is_upstream else "Imported Energy"
    exported_name = "Energy Returned" if is_upstream else "Exported Energy"

    table: dict[str, dict[str, Any]] = {
        "l1-current": {
            "platform": Platform.SENSOR,
            "name": "L1 Current",
            "device_class": SensorDeviceClass.CURRENT,
            "state_class": SensorStateClass.MEASUREMENT,
            "native_unit": UnitOfElectricCurrent.AMPERE,
        },
        "l2-current": {
            "platform": Platform.SENSOR,
            "name": "L2 Current",
            "device_class": SensorDeviceClass.CURRENT,
            "state_class": SensorStateClass.MEASUREMENT,
            "native_unit": UnitOfElectricCurrent.AMPERE,
        },
        "active-power": {
            "platform": Platform.SENSOR,
            "name": "Power",
            "device_class": SensorDeviceClass.POWER,
            "state_class": SensorStateClass.MEASUREMENT,
            "native_unit": UnitOfPower.WATT,
        },
        "imported-energy": {
            "platform": Platform.SENSOR,
            "name": imported_name,
            "device_class": SensorDeviceClass.ENERGY,
            "state_class": SensorStateClass.TOTAL_INCREASING,
            "native_unit": UnitOfEnergy.WATT_HOUR,
        },
        "exported-energy": {
            "platform": Platform.SENSOR,
            "name": exported_name,
            "device_class": SensorDeviceClass.ENERGY,
            "state_class": SensorStateClass.TOTAL_INCREASING,
            "native_unit": UnitOfEnergy.WATT_HOUR,
        },
    }
    return _emit_from_table(device_id, capability, properties, table)


def _map_lugs_connection(
    device_id: str,
    capability: str,
    properties: dict[str, Any],
    device_data: dict[str, Any],
) -> list[EntitySpec]:
    """Lugs connection: who feeds / is fed by this lug.

    The spec declares both ``fed-by-*`` and ``feeds-*`` triplets on every lug
    for symmetry, but each direction only populates its own half on the wire.
    Filter at the descriptor level so the user doesn't see permanently-empty
    "Feeds Device" entities on an upstream lug (and vice versa). When the
    direction hasn't yet been observed (property values not loaded — e.g.
    descriptor built before the broker delivered them), emit nothing rather
    than guess wrong; Phase 3's setup path waits for `info/direction` before
    invoking the mapper.

    The ``*-device-status`` enum (OK / LOST / DEGRADED) is the spec's
    replacement for the old ``bess/connected`` boolean. Surfaced as a PROBLEM
    binary_sensor that's "on" when status is anything other than OK — the
    natural HA-side fit for a "something's wrong" indicator.
    """
    direction = _lug_direction(device_data)

    common: dict[str, dict[str, Any]] = {
        "count": {
            "platform": Platform.SENSOR,
            "name": "Connection Count",
            "entity_category": EntityCategory.DIAGNOSTIC,
        },
    }
    upstream_only: dict[str, dict[str, Any]] = {
        "fed-by-device-id": {
            "platform": Platform.SENSOR,
            "name": "Fed By Device",
            "entity_category": EntityCategory.DIAGNOSTIC,
        },
        "fed-by-device-type": {
            "platform": Platform.SENSOR,
            "name": "Fed By Device Type",
            "entity_category": EntityCategory.DIAGNOSTIC,
        },
        "fed-by-device-status": {
            "platform": Platform.BINARY_SENSOR,
            "name": "Upstream Connection Problem",
            "device_class": BinarySensorDeviceClass.PROBLEM,
            "on_values": {"LOST", "DEGRADED"},
            "entity_category": EntityCategory.DIAGNOSTIC,
        },
    }
    downstream_only: dict[str, dict[str, Any]] = {
        "feeds-device-id": {
            "platform": Platform.SENSOR,
            "name": "Feeds Device",
            "entity_category": EntityCategory.DIAGNOSTIC,
        },
        "feeds-device-type": {
            "platform": Platform.SENSOR,
            "name": "Feeds Device Type",
            "entity_category": EntityCategory.DIAGNOSTIC,
        },
        "feeds-device-status": {
            "platform": Platform.BINARY_SENSOR,
            "name": "Downstream Connection Problem",
            "device_class": BinarySensorDeviceClass.PROBLEM,
            "on_values": {"LOST", "DEGRADED"},
            "entity_category": EntityCategory.DIAGNOSTIC,
        },
    }

    table: dict[str, dict[str, Any]]
    if direction == "upstream":
        table = {**common, **upstream_only}
    elif direction == "downstream":
        table = {**common, **downstream_only}
    else:
        return []

    return _emit_from_table(device_id, capability, properties, table)


# ─ BESS capabilities ─


def _map_bess_info(
    device_id: str, capability: str, properties: dict[str, Any], device_data: dict[str, Any]
) -> list[EntitySpec]:
    """TODO Phase 2 — BESS identity (vendor, model, serials, versions, nameplate-capacity)."""
    return []


def _map_bess_soc(
    device_id: str, capability: str, properties: dict[str, Any], device_data: dict[str, Any]
) -> list[EntitySpec]:
    """TODO Phase 2 — BESS state-of-charge (%) + state-of-energy (kWh)."""
    return []


# ─ MID grandchild capabilities ─


def _map_mid_info(
    device_id: str, capability: str, properties: dict[str, Any], device_data: dict[str, Any]
) -> list[EntitySpec]:
    """TODO Phase 2 — MID identity (mostly null on synthesized MIDs)."""
    return []


def _map_mid_grid(
    device_id: str, capability: str, properties: dict[str, Any], device_data: dict[str, Any]
) -> list[EntitySpec]:
    """TODO Phase 2 — MID grid state (islanding-state, grid-state, grid-forming-entity)."""
    return []


# ─ PV capabilities ─


def _map_pv_info(
    device_id: str, capability: str, properties: dict[str, Any], device_data: dict[str, Any]
) -> list[EntitySpec]:
    """TODO Phase 2 — PV identity (vendor, product, serial, firmware, nameplate-capacity)."""
    return []


# ─ EVSE capabilities ─


def _map_evse_info(
    device_id: str, capability: str, properties: dict[str, Any], device_data: dict[str, Any]
) -> list[EntitySpec]:
    """TODO Phase 2 — EVSE identity (vendor, product, part, serial, firmware)."""
    return []


def _map_evse_status(
    device_id: str, capability: str, properties: dict[str, Any], device_data: dict[str, Any]
) -> list[EntitySpec]:
    """TODO Phase 2 — EVSE operational-state enum (renamed from status/status)."""
    return []


def _map_evse_switch(
    device_id: str, capability: str, properties: dict[str, Any], device_data: dict[str, Any]
) -> list[EntitySpec]:
    """TODO Phase 2 — EVSE lock-state."""
    return []


def _map_evse_meter(
    device_id: str, capability: str, properties: dict[str, Any], device_data: dict[str, Any]
) -> list[EntitySpec]:
    """TODO Phase 2 — EVSE advertised-current."""
    return []


def _map_evse_config(
    device_id: str, capability: str, properties: dict[str, Any], device_data: dict[str, Any]
) -> list[EntitySpec]:
    """TODO Phase 2 — EVSE user-max-charge-current (settable) + max-charge-current."""
    return []


# ─ Circuit capabilities ─


def _map_circuit_info(
    device_id: str, capability: str, properties: dict[str, Any], device_data: dict[str, Any]
) -> list[EntitySpec]:
    """TODO Phase 2 — circuit info (name, breaker-rating, tab-number renamed from space, dipole)."""
    return []


def _map_circuit_meter(
    device_id: str, capability: str, properties: dict[str, Any], device_data: dict[str, Any]
) -> list[EntitySpec]:
    """TODO Phase 2 — circuit meter (current, active-power with negate+W override, energies)."""
    return []


def _map_circuit_switch(
    device_id: str, capability: str, properties: dict[str, Any], device_data: dict[str, Any]
) -> list[EntitySpec]:
    """TODO Phase 2 — circuit relay (settable, gated on priority/relay-controllable) + relay-requester."""
    return []


def _map_circuit_priority(
    device_id: str, capability: str, properties: dict[str, Any], device_data: dict[str, Any]
) -> list[EntitySpec]:
    """TODO Phase 2 — circuit shed-priority (settable), pcs-managed, pcs-priority, relay-controllable."""
    return []


def _map_circuit_connection(
    device_id: str, capability: str, properties: dict[str, Any], device_data: dict[str, Any]
) -> list[EntitySpec]:
    """TODO Phase 2 — circuit connection (feeds-device-id/-type/-status, count)."""
    return []


# ── Dispatcher ────────────────────────────────────────────────────────────

CAPABILITY_MAPPERS: dict[tuple[str, str], Mapper] = {
    # Panel root (distribution-enclosure)
    (DEVICE_TYPE_DISTRIBUTION_ENCLOSURE, CAPABILITY_INFO): _map_enclosure_info,
    (DEVICE_TYPE_DISTRIBUTION_ENCLOSURE, CAPABILITY_DOOR): _map_enclosure_door,
    (DEVICE_TYPE_DISTRIBUTION_ENCLOSURE, CAPABILITY_METER): _map_enclosure_meter,
    (DEVICE_TYPE_DISTRIBUTION_ENCLOSURE, CAPABILITY_STATUS): _map_enclosure_status,
    (DEVICE_TYPE_DISTRIBUTION_ENCLOSURE, CAPABILITY_PCS): _map_enclosure_pcs,
    (DEVICE_TYPE_DISTRIBUTION_ENCLOSURE, CAPABILITY_POWER_FLOWS): _map_enclosure_power_flows,
    (DEVICE_TYPE_DISTRIBUTION_ENCLOSURE, CAPABILITY_SHED_FORECAST): _map_enclosure_shed_forecast,
    (DEVICE_TYPE_DISTRIBUTION_ENCLOSURE, CAPABILITY_SHED): _map_enclosure_shed,
    # Lugs (upstream and downstream share the same mappers)
    (DEVICE_TYPE_LUGS, CAPABILITY_INFO): _map_lugs_info,
    (DEVICE_TYPE_LUGS, CAPABILITY_METER): _map_lugs_meter,
    (DEVICE_TYPE_LUGS, CAPABILITY_CONNECTION): _map_lugs_connection,
    # BESS
    (DEVICE_TYPE_BESS, CAPABILITY_INFO): _map_bess_info,
    (DEVICE_TYPE_BESS, CAPABILITY_SOC): _map_bess_soc,
    # MID grandchild
    (DEVICE_TYPE_MID, CAPABILITY_INFO): _map_mid_info,
    (DEVICE_TYPE_MID, CAPABILITY_GRID): _map_mid_grid,
    # PV (info only in v1; meter is omitted — see design doc)
    (DEVICE_TYPE_PV, CAPABILITY_INFO): _map_pv_info,
    # EVSE
    (DEVICE_TYPE_EVSE, CAPABILITY_INFO): _map_evse_info,
    (DEVICE_TYPE_EVSE, CAPABILITY_STATUS): _map_evse_status,
    (DEVICE_TYPE_EVSE, CAPABILITY_SWITCH): _map_evse_switch,
    (DEVICE_TYPE_EVSE, CAPABILITY_METER): _map_evse_meter,
    (DEVICE_TYPE_EVSE, CAPABILITY_CONFIG): _map_evse_config,
    # Circuit
    (DEVICE_TYPE_CIRCUIT, CAPABILITY_INFO): _map_circuit_info,
    (DEVICE_TYPE_CIRCUIT, CAPABILITY_METER): _map_circuit_meter,
    (DEVICE_TYPE_CIRCUIT, CAPABILITY_SWITCH): _map_circuit_switch,
    (DEVICE_TYPE_CIRCUIT, CAPABILITY_PRIORITY): _map_circuit_priority,
    (DEVICE_TYPE_CIRCUIT, CAPABILITY_CONNECTION): _map_circuit_connection,
}


def device_type_short(homie_type: str) -> str | None:
    """Extract the short device-class name from a Homie device-type URI.

    Returns ``"distribution-enclosure"`` for ``"energy.ebus.device.distribution-enclosure"``;
    returns None for URIs that don't carry the eBus prefix (the caller should
    skip those).
    """
    if not homie_type.startswith(HOMIE_DEVICE_TYPE_PREFIX):
        return None
    return homie_type[len(HOMIE_DEVICE_TYPE_PREFIX):]


def entities_from_tree(devices: dict[str, dict[str, Any]]) -> list[EntitySpec]:
    """Walk a tree of Homie devices and emit EntitySpecs for every known capability.

    Input shape mirrors what ``ebus_sdk.Controller`` produces in tree-rooted mode
    (and what the tree-v1 snapshot JSONs in ``tests/fixtures/tree/`` carry under
    the ``devices`` key): a mapping ``device_id -> {description, properties,
    root_id, parent_id, children_ids, is_root}``.

    Capabilities with no mapper registered are logged at DEBUG and skipped —
    forward-compatible with spec additions.
    """
    specs: list[EntitySpec] = []
    for device_id, device_data in devices.items():
        description = device_data.get("description") or {}
        device_type = description.get("type", "")
        device_class = device_type_short(device_type)
        if device_class is None:
            _LOGGER.debug(
                "device %s has non-eBus type %r; skipping", device_id, device_type
            )
            continue

        for capability, node_desc in (description.get("nodes") or {}).items():
            mapper = CAPABILITY_MAPPERS.get((device_class, capability))
            if mapper is None:
                _LOGGER.debug(
                    "no mapper for (%s, %s) on device %s",
                    device_class,
                    capability,
                    device_id,
                )
                continue
            properties = (node_desc or {}).get("properties") or {}
            specs.extend(mapper(device_id, capability, properties, device_data))

    return specs
