"""Map Homie 5 parent/child tree → Home Assistant entity descriptors.

See ``docs/g3p-23496-migration.md`` and the canonical property-mapping CSV at
``~/projects/span.io/shadow-repo/device/doc/g3p-23496-property-mapping.csv``
for the per-capability mapping table.

The dispatcher walks a tree of Homie devices (the same shape that
``ebus_sdk.Controller`` exposes when constructed with ``root_device_id=``, and
the same shape that the tree-v1 snapshot JSONs in ``tests/fixtures/tree/``
use) and routes each capability node through a per-(device-class, capability)
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

    # When True, the sensor suppresses ``negate`` at runtime if the owning
    # circuit feeds a PV device (``connection/feeds-device-type == pv``). PV-feed
    # circuits already report generation as positive, so the load sign-flip must
    # not apply. Decided at runtime, not build time, because feeds-device-type
    # (a retained sibling property) may not have landed when entities are built.
    pv_sign_aware: bool = False

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


def _lug_direction(device_data: dict[str, Any], device_id: str = "") -> str:
    """Return the lug's direction ('upstream'/'downstream'/'').

    Prefers the runtime ``info/direction`` property when it's been delivered
    (publisher's authoritative source — handles future device-id renames or
    publisher-specific id conventions). Falls back to parsing the device-id
    suffix (``-lugs-up`` / ``-lugs-dn``) when the property hasn't been
    observed yet. Without the fallback, the lugs ``connection`` mapper would
    silently emit zero entities on the first integration setup pass —
    property values arrive asynchronously after the description, and the
    initial ``entities_from_tree`` walk often runs before ``info/direction``
    has been delivered. The fallback also lets the meter mapper pick the
    correct user-friendly energy-entity names ("Energy" / "Energy Returned"
    on upstream) without waiting for the property.

    Returns the empty string only when neither source resolves the direction.
    """
    raw = device_data.get("properties", {}).get("info/direction", "")
    if raw:
        return str(raw).lower()
    if device_id.endswith("-lugs-up"):
        return "upstream"
    if device_id.endswith("-lugs-dn"):
        return "downstream"
    return ""


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
    is_upstream = _lug_direction(device_data, device_id) == "upstream"
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
    direction = _lug_direction(device_data, device_id)

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


_INFO_TEXT_FIELDS: dict[str, str] = {
    "vendor-name": "Vendor",
    "product-name": "Product",
    "model": "Model",
    "serial-number": "Serial Number",
    "hardware-version": "Hardware Version",
}


def _info_text_table(
    *include: str,
    firmware: bool = True,
) -> dict[str, dict[str, Any]]:
    """Build a property-mapping table for a device's ``info`` capability.

    Most ``info`` capabilities (panel, BESS, MID, PV, EVSE) share the same
    shape — vendor / product / model / serial / version strings exposed as
    diagnostic text sensors. ``include`` selects which fixed text fields go
    into the table; ``firmware=True`` adds both ``firmware-version`` (spec)
    and ``software-version`` (legacy) under the same "Firmware Version" label
    so in-flight firmware renames don't leave the entity floating.
    """
    table: dict[str, dict[str, Any]] = {
        prop_id: {
            "platform": Platform.SENSOR,
            "name": _INFO_TEXT_FIELDS[prop_id],
            "entity_category": EntityCategory.DIAGNOSTIC,
        }
        for prop_id in include
    }
    if firmware:
        table["firmware-version"] = {
            "platform": Platform.SENSOR,
            "name": "Firmware Version",
            "entity_category": EntityCategory.DIAGNOSTIC,
        }
        table["software-version"] = {
            "platform": Platform.SENSOR,
            "name": "Firmware Version",
            "entity_category": EntityCategory.DIAGNOSTIC,
        }
    return table


def _map_bess_info(
    device_id: str,
    capability: str,
    properties: dict[str, Any],
    device_data: dict[str, Any],
) -> list[EntitySpec]:
    """BESS identity: vendor, product, model, serial, firmware, nameplate capacity.

    ``nameplate-capacity`` (kWh) is the storage capacity rated by the vendor —
    static, not a real-time reading, so no state_class. ENERGY_STORAGE device
    class is HA's idiomatic fit for "this battery holds N kWh".
    """
    table = _info_text_table("vendor-name", "product-name", "model", "serial-number", "hardware-version")
    table["nameplate-capacity"] = {
        "platform": Platform.SENSOR,
        "name": "Nameplate Capacity",
        "device_class": SensorDeviceClass.ENERGY_STORAGE,
        "native_unit": UnitOfEnergy.KILO_WATT_HOUR,
        "entity_category": EntityCategory.DIAGNOSTIC,
    }
    return _emit_from_table(device_id, capability, properties, table)


def _map_bess_soc(
    device_id: str,
    capability: str,
    properties: dict[str, Any],
    device_data: dict[str, Any],
) -> list[EntitySpec]:
    """BESS state-of-charge (%) and state-of-energy (kWh).

    Both are MEASUREMENT — instantaneous values that fluctuate up and down as
    the battery cycles, not cumulative counters. ``soc`` uses HA's BATTERY
    device class (the canonical "battery percentage" surface) while ``soe``
    uses ENERGY_STORAGE (the current stored energy in kWh).
    """
    table: dict[str, dict[str, Any]] = {
        "soc": {
            "platform": Platform.SENSOR,
            "name": "State of Charge",
            "device_class": SensorDeviceClass.BATTERY,
            "state_class": SensorStateClass.MEASUREMENT,
            "native_unit": PERCENTAGE,
        },
        "soe": {
            "platform": Platform.SENSOR,
            "name": "State of Energy",
            "device_class": SensorDeviceClass.ENERGY_STORAGE,
            "state_class": SensorStateClass.MEASUREMENT,
            "native_unit": UnitOfEnergy.KILO_WATT_HOUR,
        },
    }
    return _emit_from_table(device_id, capability, properties, table)


# ─ MID grandchild capabilities ─


def _map_mid_info(
    device_id: str,
    capability: str,
    properties: dict[str, Any],
    device_data: dict[str, Any],
) -> list[EntitySpec]:
    """MID identity — same six-field text surface as the BESS info capability.

    On a synthesized MID (every commissioned BESS gets one; Tesla Powerwall etc.
    don't expose a separable MID), most of these fields are null on the wire
    per spec. The mapper still emits the entities; HA treats null as
    ``unknown``, which is the right surface.
    """
    table = _info_text_table("vendor-name", "product-name", "model", "serial-number", "hardware-version")
    return _emit_from_table(device_id, capability, properties, table)


def _map_mid_grid(
    device_id: str,
    capability: str,
    properties: dict[str, Any],
    device_data: dict[str, Any],
) -> list[EntitySpec]:
    """MID grid state.

    ``islanding-state`` (ON_GRID / OFF_GRID / UNKNOWN) is the operational
    indicator of whether the panel is currently grid-tied or islanded.
    ``grid-state`` (UP / DOWN / DEGRADED / UNKNOWN) is the utility-side health
    summary. ``grid-forming-entity`` is the device-id of whatever is currently
    establishing the grid — ``"GRID"`` when grid-tied, the BESS Homie device-id
    when islanded. All three are text sensors to preserve the full enum value
    (a PROBLEM binary would collapse DEGRADED into not-OK and lose information).
    """
    table: dict[str, dict[str, Any]] = {
        "islanding-state": {
            "platform": Platform.SENSOR,
            "name": "Islanding State",
            "icon": "mdi:transmission-tower-export",
        },
        "grid-state": {
            "platform": Platform.SENSOR,
            "name": "Grid State",
            "icon": "mdi:transmission-tower",
        },
        "grid-forming-entity": {
            "platform": Platform.SENSOR,
            "name": "Grid Forming Entity",
            "entity_category": EntityCategory.DIAGNOSTIC,
        },
    }
    return _emit_from_table(device_id, capability, properties, table)


# ─ PV capabilities ─


def _map_pv_info(
    device_id: str,
    capability: str,
    properties: dict[str, Any],
    device_data: dict[str, Any],
) -> list[EntitySpec]:
    """PV identity: vendor, product, serial, firmware, nameplate capacity (W).

    For SPAN G2 deployments where the inverter serial isn't surfaced by the
    SOLAR_INVERTER cloud shadow, ``serial-number`` may be null on the wire —
    the entity still publishes (HA treats null as ``unknown``). PV info has
    no ``model`` or ``hardware-version`` rows on the wire.

    ``nameplate-capacity`` is now declared in the correct unit (W) on the
    tree-v1 publisher — the firmware bug that affected the legacy data model
    (declared kW, actual W) has been fixed alongside the data-model migration,
    so no override is needed here.
    """
    table = _info_text_table("vendor-name", "product-name", "serial-number")
    table["nameplate-capacity"] = {
        "platform": Platform.SENSOR,
        "name": "Nameplate Capacity",
        "device_class": SensorDeviceClass.POWER,
        "native_unit": UnitOfPower.WATT,
        "entity_category": EntityCategory.DIAGNOSTIC,
    }
    return _emit_from_table(device_id, capability, properties, table)


# ─ EVSE capabilities ─


def _map_evse_info(
    device_id: str,
    capability: str,
    properties: dict[str, Any],
    device_data: dict[str, Any],
) -> list[EntitySpec]:
    """EVSE identity: vendor, product, part-number, serial, firmware-version.

    ``part-number`` is unique to EVSE info (not present on BESS / MID / PV);
    everything else is the standard text-field shape factored through
    ``_info_text_table``. There's no ``model`` row on the EVSE info node.
    """
    table = _info_text_table("vendor-name", "product-name", "serial-number")
    table["part-number"] = {
        "platform": Platform.SENSOR,
        "name": "Part Number",
        "entity_category": EntityCategory.DIAGNOSTIC,
    }
    return _emit_from_table(device_id, capability, properties, table)


def _map_evse_status(
    device_id: str,
    capability: str,
    properties: dict[str, Any],
    device_data: dict[str, Any],
) -> list[EntitySpec]:
    """EVSE operational state (was ``status/status`` in the flat data model).

    Surfaces the enum text directly so the value space (which is publisher-
    defined and may add states in future firmware) reads through verbatim.
    """
    if "operational-state" not in properties:
        return []
    return [
        EntitySpec(
            device_id=device_id,
            capability=capability,
            property_id="operational-state",
            platform=Platform.SENSOR,
            name="Status",
            icon="mdi:ev-station",
        )
    ]


def _map_evse_switch(
    device_id: str,
    capability: str,
    properties: dict[str, Any],
    device_data: dict[str, Any],
) -> list[EntitySpec]:
    """EVSE lock-state — the cable lock indicator (read-only per spec).

    The EVSE controls the lock during a charging session; the user can't write
    it. Surfaced as a text sensor showing the enum value rather than as a
    binary so consumers can distinguish all states (LOCKED, UNLOCKED, plus any
    UNKNOWN / FAULT states the publisher may add).
    """
    if "lock-state" not in properties:
        return []
    return [
        EntitySpec(
            device_id=device_id,
            capability=capability,
            property_id="lock-state",
            platform=Platform.SENSOR,
            name="Lock State",
            icon="mdi:lock",
        )
    ]


def _map_evse_meter(
    device_id: str,
    capability: str,
    properties: dict[str, Any],
    device_data: dict[str, Any],
) -> list[EntitySpec]:
    """EVSE advertised-current — the current the EVSE is offering to the car.

    Distinct from ``config/max-charge-current`` (the hardware ceiling) and
    ``config/user-max-charge-current`` (the user-imposed limit); the EVSE's
    advertisement is the lower of those minus any in-session derate.
    """
    table: dict[str, dict[str, Any]] = {
        "advertised-current": {
            "platform": Platform.SENSOR,
            "name": "Advertised Current",
            "device_class": SensorDeviceClass.CURRENT,
            "state_class": SensorStateClass.MEASUREMENT,
            "native_unit": UnitOfElectricCurrent.AMPERE,
        },
    }
    return _emit_from_table(device_id, capability, properties, table)


def _map_evse_config(
    device_id: str,
    capability: str,
    properties: dict[str, Any],
    device_data: dict[str, Any],
) -> list[EntitySpec]:
    """EVSE config: user-max-charge-current (settable) + max-charge-current.

    ``user-max-charge-current`` is settable, with a dynamic publisher-provided
    ``$format = "<lower>:<max-charge-current>"`` that bounds the writable
    range. For now it surfaces as a read-only diagnostic sensor — Phase 3
    will add Platform.NUMBER support to make it settable from the HA UI.
    ``max-charge-current`` is the hardware ceiling (static, diagnostic).
    """
    table: dict[str, dict[str, Any]] = {
        "user-max-charge-current": {
            "platform": Platform.SENSOR,
            "name": "User Max Charge Current",
            "device_class": SensorDeviceClass.CURRENT,
            "native_unit": UnitOfElectricCurrent.AMPERE,
            "entity_category": EntityCategory.DIAGNOSTIC,
        },
        "max-charge-current": {
            "platform": Platform.SENSOR,
            "name": "Max Charge Current",
            "device_class": SensorDeviceClass.CURRENT,
            "native_unit": UnitOfElectricCurrent.AMPERE,
            "entity_category": EntityCategory.DIAGNOSTIC,
        },
    }
    return _emit_from_table(device_id, capability, properties, table)


# ─ Circuit capabilities ─


def _map_circuit_info(
    device_id: str,
    capability: str,
    properties: dict[str, Any],
    device_data: dict[str, Any],
) -> list[EntitySpec]:
    """Circuit info: name (settable), breaker-rating, tab-number, dipole.

    The spec renames the panel-position property from ``space`` to ``tab-number``;
    snapshots predating the firmware-side rename still publish ``space``. The
    mapper handles both with a single "Tab Number" label (same in-flight-rename
    pattern as door/state, info/firmware-version, status/cloud-connection).

    ``name`` is settable per spec (users edit circuit labels in the SPAN app
    and the panel republishes), but Phase 2 surfaces it as a read-only
    diagnostic sensor; the HA device's friendly name is the consumer of this
    value (Phase 3 wires that up via the device-name lookup), so there's no
    need to expose a settable text-field entity to the HA UI.

    The v1 spec also defines optional ``dedicated`` (3-state), ``tags``, and
    ``external-ids`` info fields, but the SPAN publisher omits them and the
    relevant registries aren't shipped yet — not mapped.
    """
    table: dict[str, dict[str, Any]] = {
        "name": {
            "platform": Platform.SENSOR,
            "name": "Name",
            "entity_category": EntityCategory.DIAGNOSTIC,
        },
        "breaker-rating": {
            "platform": Platform.SENSOR,
            "name": "Breaker Rating",
            "device_class": SensorDeviceClass.CURRENT,
            "native_unit": UnitOfElectricCurrent.AMPERE,
            "entity_category": EntityCategory.DIAGNOSTIC,
        },
        # Spec name + legacy name for the panel-position property.
        "tab-number": {
            "platform": Platform.SENSOR,
            "name": "Tab Number",
            "entity_category": EntityCategory.DIAGNOSTIC,
        },
        "space": {
            "platform": Platform.SENSOR,
            "name": "Tab Number",
            "entity_category": EntityCategory.DIAGNOSTIC,
        },
        "dipole": {
            "platform": Platform.BINARY_SENSOR,
            "name": "Dipole",
            "entity_category": EntityCategory.DIAGNOSTIC,
        },
    }
    return _emit_from_table(device_id, capability, properties, table)


def _map_circuit_meter(
    device_id: str,
    capability: str,
    properties: dict[str, Any],
    device_data: dict[str, Any],
) -> list[EntitySpec]:
    """Circuit meter: current (A), power (W with sign-flip), energies (Wh, total_increasing).

    ``active-power``: the firmware bug that declared kW-but-published-W on the
    legacy data model appears to be fixed in tree-v1 (publishers now declare
    W). The mapper hardcodes W regardless of what the description says, so a
    panel that hasn't taken the fix still surfaces correctly. For a load
    circuit, raw eBus reports consumption as negative, so ``negate=True``
    flips the sign to positive = consumption (matching HA's device_consumption
    convention in the Energy Dashboard Now-tab Sankey).

    A circuit commissioned as feeding a PV inverter is the exception: raw eBus
    already reports generation as POSITIVE, agreeing in sign with the circuit's
    ``imported-energy`` (= backfeed/generation) counter. Negating it would
    publish a negative power sensor while the array produces — the HA Energy
    Dashboard clamps that to zero (flat solar band) and any downstream consumer
    using the sensor as a production signal gets the wrong sign (SPAN-s48). The
    PV exception is gated by ``pv_sign_aware``, which the sensor evaluates at
    runtime against the live ``connection/feeds-device-type`` — NOT at build
    time, because that retained sibling property is not guaranteed to have
    landed when entities are first constructed (setup waits for descendant
    descriptions + circuit names, not for connection values).

    ``imported-energy`` / ``exported-energy``: SPAN's panel-perspective
    convention — ``exported-energy`` is energy delivered TO the circuit
    (= consumption from a load circuit's POV, the dominant counter), and
    ``imported-energy`` is energy flowing BACK from the circuit (= backfeed,
    typically near zero unless the circuit feeds a PV inverter). Named in
    user-friendly terms per the README (and the legacy mapper).
    Monotonicity workaround still required per [AN-001](appnote-AN001-energy-counter-monotonicity.md).
    """
    table: dict[str, dict[str, Any]] = {
        "current": {
            "platform": Platform.SENSOR,
            "name": "Current",
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
            "negate": True,
            "pv_sign_aware": True,
        },
        "imported-energy": {
            "platform": Platform.SENSOR,
            "name": "Energy Returned",
            "device_class": SensorDeviceClass.ENERGY,
            "state_class": SensorStateClass.TOTAL_INCREASING,
            "native_unit": UnitOfEnergy.WATT_HOUR,
        },
        "exported-energy": {
            "platform": Platform.SENSOR,
            "name": "Energy",
            "device_class": SensorDeviceClass.ENERGY,
            "state_class": SensorStateClass.TOTAL_INCREASING,
            "native_unit": UnitOfEnergy.WATT_HOUR,
        },
    }
    return _emit_from_table(device_id, capability, properties, table)


def _map_circuit_switch(
    device_id: str,
    capability: str,
    properties: dict[str, Any],
    device_data: dict[str, Any],
) -> list[EntitySpec]:
    """Circuit switch: relay (settable, gated) + relay-requester (enum sensor).

    ``relay`` is a settable enum (UNKNOWN / OPEN / CLOSED) — surfaced as
    Platform.SWITCH. Per spec the publisher gates ``$settable`` at runtime
    based on ``priority/relay-controllable``; Phase 2 reads that value from
    the device's loaded properties (``device_data["properties"]``) to decide
    whether the spec carries ``settable=True``. When the property hasn't been
    observed yet (e.g. descriptor constructed before the broker delivered
    values), default to settable=True — the publisher will refuse the write
    if relay-controllable is false, which is the spec-correct fallback.
    Future re-gating on property-change is a Phase 3+ enhancement.

    ``relay-requester`` is a read-only enum showing who last requested the
    current relay state (NONE / USER / LOAD_SHED / PCS / CONFIGURATION /
    FAULT / UNKNOWN — per the realigned BranchRequester domain). Surfaced
    as a text sensor so the full enum value reads through.
    """
    specs: list[EntitySpec] = []
    if "relay" in properties:
        # Look up relay-controllable from current values; default True when unknown.
        relay_controllable = device_data.get("properties", {}).get(
            "priority/relay-controllable", True
        )
        settable = bool(relay_controllable) if relay_controllable is not None else True
        specs.append(
            EntitySpec(
                device_id=device_id,
                capability=capability,
                property_id="relay",
                platform=Platform.SWITCH,
                name="Relay",
                icon="mdi:electric-switch",
                settable=settable,
            )
        )
    if "relay-requester" in properties:
        specs.append(
            EntitySpec(
                device_id=device_id,
                capability=capability,
                property_id="relay-requester",
                platform=Platform.SENSOR,
                name="Relay Requester",
                entity_category=EntityCategory.DIAGNOSTIC,
            )
        )
    return specs


def _map_circuit_priority(
    device_id: str,
    capability: str,
    properties: dict[str, Any],
    device_data: dict[str, Any],
) -> list[EntitySpec]:
    """Circuit priority: shed-priority (settable, gated) + pcs-* + relay-controllable.

    ``shed-priority`` is the user-facing shed-priority select (UNKNOWN /
    OFF_GRID / SOC_THRESHOLD / NEVER). Per spec the publisher gates
    ``$settable`` per-circuit — false when a circuit is commissioned as
    permanent OFF_GRID — so the mapper reads ``shed-priority-settable`` (an
    internal-only sibling property the publisher derives) from
    ``device_data["properties"]`` when available; default settable=True when
    unknown.

    ``pcs-managed`` (boolean diagnostic), ``pcs-priority`` (integer
    diagnostic), and ``relay-controllable`` (boolean diagnostic — the
    polarity-flipped successor to the legacy ``alwaysOn`` field; True means
    the relay can be commanded, False means it's locked open or closed by
    configuration) round out the capability.
    """
    specs: list[EntitySpec] = []
    if "shed-priority" in properties:
        # The publisher's "shed-priority-settable" is internal-only (not in
        # $description); when it's present in property values, honour it.
        shed_settable = device_data.get("properties", {}).get(
            "priority/shed-priority-settable", True
        )
        options = _parse_enum_format(properties["shed-priority"].get("format", ""))
        specs.append(
            EntitySpec(
                device_id=device_id,
                capability=capability,
                property_id="shed-priority",
                platform=Platform.SELECT,
                name="Shed Priority",
                icon="mdi:priority-high",
                settable=bool(shed_settable) if shed_settable is not None else True,
                options=options,
            )
        )
    table: dict[str, dict[str, Any]] = {
        "pcs-managed": {
            "platform": Platform.BINARY_SENSOR,
            "name": "PCS Managed",
            "entity_category": EntityCategory.DIAGNOSTIC,
        },
        "pcs-priority": {
            "platform": Platform.SENSOR,
            "name": "PCS Priority",
            "entity_category": EntityCategory.DIAGNOSTIC,
        },
        "relay-controllable": {
            "platform": Platform.BINARY_SENSOR,
            "name": "Relay Controllable",
            "entity_category": EntityCategory.DIAGNOSTIC,
        },
    }
    specs.extend(_emit_from_table(device_id, capability, properties, table))
    return specs


def _map_circuit_connection(
    device_id: str,
    capability: str,
    properties: dict[str, Any],
    device_data: dict[str, Any],
) -> list[EntitySpec]:
    """Circuit connection: who this circuit feeds (PV, IN_PANEL BESS, EVSE).

    Populated only on circuits commissioned as feeding a specific DER. The
    ``feeds-device-status`` enum (OK / LOST / DEGRADED) is the replacement
    for the retired ``bess/connected`` boolean for the IN_PANEL case — same
    PROBLEM-binary treatment as lugs/connection so a not-OK state surfaces
    as an actionable HA indicator.

    Other circuits (the vast majority — kitchen, server rack, lighting)
    publish the capability with null values across the board; the descriptor
    still creates the entities and HA renders them as ``unknown``.
    """
    table: dict[str, dict[str, Any]] = {
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
            "name": "Feeds Connection Problem",
            "device_class": BinarySensorDeviceClass.PROBLEM,
            "on_values": {"LOST", "DEGRADED"},
            "entity_category": EntityCategory.DIAGNOSTIC,
        },
        "count": {
            "platform": Platform.SENSOR,
            "name": "Feeds Count",
            "entity_category": EntityCategory.DIAGNOSTIC,
        },
    }
    return _emit_from_table(device_id, capability, properties, table)


def _parse_enum_format(fmt: str) -> list[str]:
    """Split a Homie enum $format string ('A,B,C') into a clean list."""
    if not fmt:
        return []
    return [v.strip() for v in fmt.split(",") if v.strip()]


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
