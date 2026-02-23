"""Map Homie $description nodes/properties to Home Assistant entity descriptors."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    Platform,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class EntitySpec:
    """Descriptor for an entity to be created from a Homie property."""

    platform: Platform
    node_id: str
    property_id: str
    name: str
    # Sensor-specific
    device_class: Any | None = None
    state_class: SensorStateClass | None = None
    native_unit: str | None = None
    entity_category: EntityCategory | None = None
    # Select-specific
    options: list[str] = field(default_factory=list)
    # Value transform: negate numeric values (e.g. SPAN circuit active-power
    # reports negative for consumption, but HA convention is positive)
    negate: bool = False
    # Switch/binary_sensor
    icon: str | None = None
    # Whether this property is settable (for switches/selects)
    settable: bool = False
    # Binary sensor: values (uppercase) that mean "on"; empty = use default truthy set
    on_values: set[str] = field(default_factory=set)
    # Sub-device grouping
    node_type: str = ""
    device_name: str = ""


# ── Known abbreviations for _humanize ─────────────────────────────────────

_ABBREVIATIONS = {"pv": "PV", "ev": "EV", "soc": "SOC", "soe": "SOE", "pcs": "PCS"}


# ── Property mappers by node type ─────────────────────────────────────────

def _map_core_properties(
    node_id: str, properties: dict[str, Any]
) -> list[EntitySpec]:
    """Map core node properties to entity specs."""
    specs: list[EntitySpec] = []

    prop_map: dict[str, tuple[Platform, dict[str, Any]]] = {
        "door": (
            Platform.BINARY_SENSOR,
            {
                "name": "Door",
                "device_class": BinarySensorDeviceClass.TAMPER,
                "on_values": {"OPEN"},
            },
        ),
        "ethernet": (
            Platform.BINARY_SENSOR,
            {
                "name": "Ethernet",
                "device_class": BinarySensorDeviceClass.CONNECTIVITY,
                "entity_category": EntityCategory.DIAGNOSTIC,
            },
        ),
        "wifi": (
            Platform.BINARY_SENSOR,
            {
                "name": "Wi-Fi",
                "device_class": BinarySensorDeviceClass.CONNECTIVITY,
                "entity_category": EntityCategory.DIAGNOSTIC,
            },
        ),
        "cellular": (
            Platform.BINARY_SENSOR,
            {
                "name": "Cellular",
                "device_class": BinarySensorDeviceClass.CONNECTIVITY,
                "entity_category": EntityCategory.DIAGNOSTIC,
            },
        ),
        "software-version": (
            Platform.SENSOR,
            {
                "name": "Firmware Version",
                "entity_category": EntityCategory.DIAGNOSTIC,
            },
        ),
        "hardware-version": (
            Platform.SENSOR,
            {
                "name": "Hardware Version",
                "entity_category": EntityCategory.DIAGNOSTIC,
            },
        ),
        "dominant-power-source": (
            Platform.SELECT,
            {
                "name": "Dominant Power Source",
                "icon": "mdi:lightning-bolt",
                "settable": True,
            },
        ),
        "relay": (
            Platform.BINARY_SENSOR,
            {
                "name": "Main Relay",
                "icon": "mdi:electric-switch",
                "on_values": {"CLOSED"},
            },
        ),
        "l1-voltage": (
            Platform.SENSOR,
            {
                "name": "L1 Voltage",
                "device_class": SensorDeviceClass.VOLTAGE,
                "state_class": SensorStateClass.MEASUREMENT,
                "native_unit": UnitOfElectricPotential.VOLT,
            },
        ),
        "l2-voltage": (
            Platform.SENSOR,
            {
                "name": "L2 Voltage",
                "device_class": SensorDeviceClass.VOLTAGE,
                "state_class": SensorStateClass.MEASUREMENT,
                "native_unit": UnitOfElectricPotential.VOLT,
            },
        ),
        "breaker-rating": (
            Platform.SENSOR,
            {
                "name": "Main Breaker Rating",
                "device_class": SensorDeviceClass.CURRENT,
                "native_unit": UnitOfElectricCurrent.AMPERE,
                "entity_category": EntityCategory.DIAGNOSTIC,
            },
        ),
        "grid-islandable": (
            Platform.BINARY_SENSOR,
            {
                "name": "Grid Islandable",
                "entity_category": EntityCategory.DIAGNOSTIC,
            },
        ),
        "wifi-ssid": (
            Platform.SENSOR,
            {
                "name": "Wi-Fi SSID",
                "entity_category": EntityCategory.DIAGNOSTIC,
            },
        ),
        "vendor-cloud": (
            Platform.SENSOR,
            {
                "name": "Cloud Connection",
                "entity_category": EntityCategory.DIAGNOSTIC,
            },
        ),
        "vendor-name": (
            Platform.SENSOR,
            {
                "name": "Vendor",
                "entity_category": EntityCategory.DIAGNOSTIC,
            },
        ),
        "serial-number": (
            Platform.SENSOR,
            {
                "name": "Serial Number",
                "entity_category": EntityCategory.DIAGNOSTIC,
            },
        ),
        "postal-code": (
            Platform.SENSOR,
            {
                "name": "Postal Code",
                "entity_category": EntityCategory.DIAGNOSTIC,
            },
        ),
        "time-zone": (
            Platform.SENSOR,
            {
                "name": "Time Zone",
                "entity_category": EntityCategory.DIAGNOSTIC,
            },
        ),
    }

    for prop_id, prop_desc in properties.items():
        if prop_id in prop_map:
            platform, kwargs = prop_map[prop_id]
            if platform == Platform.SELECT:
                kwargs = {**kwargs, "options": _parse_enum_format(prop_desc.get("format", ""))}
            specs.append(
                EntitySpec(
                    platform=platform,
                    node_id=node_id,
                    property_id=prop_id,
                    **kwargs,
                )
            )

    return specs


def _map_circuit_properties(
    node_id: str,
    properties: dict[str, Any],
) -> list[EntitySpec]:
    """Map circuit node properties to entity specs.

    Entity names are relative to the sub-device (e.g. "Power" not "Kitchen Power").
    HA's has_entity_name=True prepends the device name automatically.
    """
    specs: list[EntitySpec] = []

    for prop_id, prop_desc in properties.items():
        settable = prop_desc.get("settable", False)

        if prop_id == "relay":
            specs.append(EntitySpec(
                platform=Platform.SWITCH,
                node_id=node_id,
                property_id=prop_id,
                name="Relay",
                settable=settable,
                icon="mdi:electric-switch",
            ))
        elif prop_id == "shed-priority":
            options = _parse_enum_format(prop_desc.get("format", ""))
            specs.append(EntitySpec(
                platform=Platform.SELECT,
                node_id=node_id,
                property_id=prop_id,
                name="Shed Priority",
                settable=settable,
                options=options,
                icon="mdi:priority-high",
            ))
        elif prop_id == "pcs-priority":
            specs.append(EntitySpec(
                platform=Platform.SENSOR,
                node_id=node_id,
                property_id=prop_id,
                name="PCS Priority",
                icon="mdi:priority-high",
            ))
        elif prop_id == "active-power":
            # Firmware bug: schema declares "kW" but values are actually in watts.
            # Override to W regardless of what $description says.
            # Negate: SPAN reports negative=consumption, but HA convention is
            # positive=consumption for device_consumption stat_rate (Now tab).
            specs.append(EntitySpec(
                platform=Platform.SENSOR,
                node_id=node_id,
                property_id=prop_id,
                name="Power",
                device_class=SensorDeviceClass.POWER,
                state_class=SensorStateClass.MEASUREMENT,
                native_unit=UnitOfPower.WATT,
                negate=True,
            ))
        elif prop_id == "current":
            specs.append(EntitySpec(
                platform=Platform.SENSOR,
                node_id=node_id,
                property_id=prop_id,
                name="Current",
                device_class=SensorDeviceClass.CURRENT,
                state_class=SensorStateClass.MEASUREMENT,
                native_unit=UnitOfElectricCurrent.AMPERE,
            ))
        elif prop_id in ("imported-energy", "exported-energy"):
            # SPAN convention (panel perspective): "exported" = energy delivered
            # TO circuit (consumption); "imported" = backfeed FROM circuit.
            name = "Energy" if "exported" in prop_id else "Energy Returned"
            specs.append(EntitySpec(
                platform=Platform.SENSOR,
                node_id=node_id,
                property_id=prop_id,
                name=name,
                device_class=SensorDeviceClass.ENERGY,
                state_class=SensorStateClass.TOTAL_INCREASING,
                native_unit=UnitOfEnergy.WATT_HOUR,
            ))
        elif prop_id == "breaker-rating":
            specs.append(EntitySpec(
                platform=Platform.SENSOR,
                node_id=node_id,
                property_id=prop_id,
                name="Breaker Rating",
                device_class=SensorDeviceClass.CURRENT,
                native_unit=UnitOfElectricCurrent.AMPERE,
                entity_category=EntityCategory.DIAGNOSTIC,
            ))
        elif prop_id == "space":
            specs.append(EntitySpec(
                platform=Platform.SENSOR,
                node_id=node_id,
                property_id=prop_id,
                name="Space",
                entity_category=EntityCategory.DIAGNOSTIC,
            ))
        elif prop_id in (
            "dipole", "pcs-managed", "sheddable", "never-backup", "always-on",
        ):
            specs.append(EntitySpec(
                platform=Platform.BINARY_SENSOR,
                node_id=node_id,
                property_id=prop_id,
                name=_humanize(prop_id),
                entity_category=EntityCategory.DIAGNOSTIC,
            ))
        elif prop_id == "relay-requester":
            specs.append(EntitySpec(
                platform=Platform.SENSOR,
                node_id=node_id,
                property_id=prop_id,
                name="Relay Requester",
                entity_category=EntityCategory.DIAGNOSTIC,
            ))

    return specs


def _map_power_flow_properties(
    node_id: str, properties: dict[str, Any]
) -> list[EntitySpec]:
    """Map power-flows node properties to entity specs."""
    specs: list[EntitySpec] = []

    for prop_id, prop_desc in properties.items():
        unit = prop_desc.get("unit", "")
        name = _humanize(prop_id)

        if unit in ("W", "kW") or "power" in prop_id:
            native_unit = UnitOfPower.KILO_WATT if unit == "kW" else UnitOfPower.WATT
            specs.append(EntitySpec(
                platform=Platform.SENSOR,
                node_id=node_id,
                property_id=prop_id,
                name=name,
                device_class=SensorDeviceClass.POWER,
                state_class=SensorStateClass.MEASUREMENT,
                native_unit=native_unit,
            ))
        elif unit == "Wh" or "energy" in prop_id:
            specs.append(EntitySpec(
                platform=Platform.SENSOR,
                node_id=node_id,
                property_id=prop_id,
                name=name,
                device_class=SensorDeviceClass.ENERGY,
                state_class=SensorStateClass.TOTAL_INCREASING,
                native_unit=UnitOfEnergy.WATT_HOUR,
            ))
        else:
            # Generic sensor for other power-flow properties
            specs.append(EntitySpec(
                platform=Platform.SENSOR,
                node_id=node_id,
                property_id=prop_id,
                name=name,
            ))

    return specs


def _map_lug_properties(
    node_id: str, properties: dict[str, Any]
) -> list[EntitySpec]:
    """Map lugs upstream/downstream node properties.

    Upstream lugs measure energy flowing into the panel (from grid or parent panel).
    Downstream lugs measure energy flowing out to circuits.
    """
    specs: list[EntitySpec] = []
    is_upstream = "upstream" in node_id
    direction = "Upstream" if is_upstream else "Downstream"

    for prop_id, prop_desc in properties.items():
        unit = prop_desc.get("unit", "")

        if prop_id in ("direction", "feed"):
            specs.append(EntitySpec(
                platform=Platform.SENSOR,
                node_id=node_id,
                property_id=prop_id,
                name=f"{direction} {_humanize(prop_id)}",
                entity_category=EntityCategory.DIAGNOSTIC,
            ))
        elif prop_id == "imported-energy":
            # Upstream imported = total panel energy; downstream imported = circuit-side
            name = "Energy" if is_upstream else "Downstream Energy"
            specs.append(EntitySpec(
                platform=Platform.SENSOR,
                node_id=node_id,
                property_id=prop_id,
                name=name,
                device_class=SensorDeviceClass.ENERGY,
                state_class=SensorStateClass.TOTAL_INCREASING,
                native_unit=UnitOfEnergy.WATT_HOUR,
            ))
        elif prop_id == "exported-energy":
            name = "Energy Returned" if is_upstream else "Downstream Energy Returned"
            specs.append(EntitySpec(
                platform=Platform.SENSOR,
                node_id=node_id,
                property_id=prop_id,
                name=name,
                device_class=SensorDeviceClass.ENERGY,
                state_class=SensorStateClass.TOTAL_INCREASING,
                native_unit=UnitOfEnergy.WATT_HOUR,
            ))
        elif unit in ("W", "kW") or "power" in prop_id:
            native_unit = UnitOfPower.KILO_WATT if unit == "kW" else UnitOfPower.WATT
            name = "Power" if is_upstream else "Downstream Power"
            specs.append(EntitySpec(
                platform=Platform.SENSOR,
                node_id=node_id,
                property_id=prop_id,
                name=name,
                device_class=SensorDeviceClass.POWER,
                state_class=SensorStateClass.MEASUREMENT,
                native_unit=native_unit,
            ))
        elif unit == "A":
            specs.append(EntitySpec(
                platform=Platform.SENSOR,
                node_id=node_id,
                property_id=prop_id,
                name=f"{direction} {_humanize(prop_id)}",
                device_class=SensorDeviceClass.CURRENT,
                state_class=SensorStateClass.MEASUREMENT,
                native_unit=UnitOfElectricCurrent.AMPERE,
            ))

    return specs


def _map_bess_properties(
    node_id: str, properties: dict[str, Any]
) -> list[EntitySpec]:
    """Map battery energy storage (bess) node properties.

    Entity names are relative to the sub-device (HA prepends device name).
    """
    specs: list[EntitySpec] = []

    prop_map: dict[str, tuple[Platform, dict[str, Any]]] = {
        "soc": (Platform.SENSOR, {
            "name": "State of Charge",
            "device_class": SensorDeviceClass.BATTERY,
            "state_class": SensorStateClass.MEASUREMENT,
            "native_unit": "%",
        }),
        "soe": (Platform.SENSOR, {
            "name": "State of Energy",
            "device_class": SensorDeviceClass.ENERGY_STORAGE,
            "state_class": SensorStateClass.MEASUREMENT,
            "native_unit": UnitOfEnergy.KILO_WATT_HOUR,
        }),
        "connected": (Platform.BINARY_SENSOR, {
            "name": "Connected",
            "device_class": BinarySensorDeviceClass.CONNECTIVITY,
        }),
        "grid-state": (Platform.SENSOR, {
            "name": "Grid State",
            "icon": "mdi:transmission-tower",
        }),
        "vendor-name": (Platform.SENSOR, {
            "name": "Vendor",
            "entity_category": EntityCategory.DIAGNOSTIC,
        }),
        "product-name": (Platform.SENSOR, {
            "name": "Product",
            "entity_category": EntityCategory.DIAGNOSTIC,
        }),
        "model": (Platform.SENSOR, {
            "name": "Model",
            "entity_category": EntityCategory.DIAGNOSTIC,
        }),
        "serial-number": (Platform.SENSOR, {
            "name": "Serial Number",
            "entity_category": EntityCategory.DIAGNOSTIC,
        }),
        "software-version": (Platform.SENSOR, {
            "name": "Firmware Version",
            "entity_category": EntityCategory.DIAGNOSTIC,
        }),
        "relative-position": (Platform.SENSOR, {
            "name": "Relative Position",
            "entity_category": EntityCategory.DIAGNOSTIC,
        }),
        "feed": (Platform.SENSOR, {
            "name": "Feed Circuit",
            "entity_category": EntityCategory.DIAGNOSTIC,
        }),
    }

    for prop_id, prop_desc in properties.items():
        if prop_id in prop_map:
            platform, kwargs = prop_map[prop_id]
            specs.append(EntitySpec(
                platform=platform,
                node_id=node_id,
                property_id=prop_id,
                **kwargs,
            ))
        elif prop_id == "nameplate-capacity":
            unit = prop_desc.get("unit", "kWh")
            native_unit = UnitOfEnergy.KILO_WATT_HOUR if unit == "kWh" else UnitOfEnergy.WATT_HOUR
            specs.append(EntitySpec(
                platform=Platform.SENSOR,
                node_id=node_id,
                property_id=prop_id,
                name="Nameplate Capacity",
                device_class=SensorDeviceClass.ENERGY_STORAGE,
                native_unit=native_unit,
                entity_category=EntityCategory.DIAGNOSTIC,
            ))
        else:
            unit = prop_desc.get("unit", "")
            if unit in ("W", "kW") or "power" in prop_id:
                native_unit = UnitOfPower.KILO_WATT if unit == "kW" else UnitOfPower.WATT
                specs.append(EntitySpec(
                    platform=Platform.SENSOR,
                    node_id=node_id,
                    property_id=prop_id,
                    name=_humanize(prop_id),
                    device_class=SensorDeviceClass.POWER,
                    state_class=SensorStateClass.MEASUREMENT,
                    native_unit=native_unit,
                ))

    return specs


def _map_pv_properties(
    node_id: str, properties: dict[str, Any]
) -> list[EntitySpec]:
    """Map PV (solar) node properties.

    Entity names are relative to the sub-device (HA prepends device name).
    """
    specs: list[EntitySpec] = []

    prop_map: dict[str, tuple[Platform, dict[str, Any]]] = {
        "vendor-name": (Platform.SENSOR, {
            "name": "Vendor",
            "entity_category": EntityCategory.DIAGNOSTIC,
        }),
        "product-name": (Platform.SENSOR, {
            "name": "Product",
            "entity_category": EntityCategory.DIAGNOSTIC,
        }),
        "serial-number": (Platform.SENSOR, {
            "name": "Serial Number",
            "entity_category": EntityCategory.DIAGNOSTIC,
        }),
        "software-version": (Platform.SENSOR, {
            "name": "Firmware Version",
            "entity_category": EntityCategory.DIAGNOSTIC,
        }),
        "relative-position": (Platform.SENSOR, {
            "name": "Relative Position",
            "entity_category": EntityCategory.DIAGNOSTIC,
        }),
        "feed": (Platform.SENSOR, {
            "name": "Feed Circuit",
            "entity_category": EntityCategory.DIAGNOSTIC,
        }),
    }

    for prop_id, prop_desc in properties.items():
        if prop_id in prop_map:
            platform, kwargs = prop_map[prop_id]
            specs.append(EntitySpec(
                platform=platform,
                node_id=node_id,
                property_id=prop_id,
                **kwargs,
            ))
        elif prop_id == "nameplate-capacity":
            unit = prop_desc.get("unit", "kW")
            native_unit = UnitOfPower.KILO_WATT if unit == "kW" else UnitOfPower.WATT
            specs.append(EntitySpec(
                platform=Platform.SENSOR,
                node_id=node_id,
                property_id=prop_id,
                name="Nameplate Capacity",
                device_class=SensorDeviceClass.POWER,
                native_unit=native_unit,
                entity_category=EntityCategory.DIAGNOSTIC,
            ))
        else:
            unit = prop_desc.get("unit", "")
            name = _humanize(prop_id)
            if unit in ("W", "kW") or "power" in prop_id:
                native_unit = UnitOfPower.KILO_WATT if unit == "kW" else UnitOfPower.WATT
                specs.append(EntitySpec(
                    platform=Platform.SENSOR,
                    node_id=node_id,
                    property_id=prop_id,
                    name=name,
                    device_class=SensorDeviceClass.POWER,
                    state_class=SensorStateClass.MEASUREMENT,
                    native_unit=native_unit,
                ))
            elif unit == "Wh" or "energy" in prop_id:
                specs.append(EntitySpec(
                    platform=Platform.SENSOR,
                    node_id=node_id,
                    property_id=prop_id,
                    name=name,
                    device_class=SensorDeviceClass.ENERGY,
                    state_class=SensorStateClass.TOTAL_INCREASING,
                    native_unit=UnitOfEnergy.WATT_HOUR,
                ))

    return specs


def _map_evse_properties(
    node_id: str, properties: dict[str, Any]
) -> list[EntitySpec]:
    """Map EV charger (evse) node properties.

    Entity names are relative to the sub-device (HA prepends device name).
    """
    specs: list[EntitySpec] = []

    prop_map: dict[str, tuple[Platform, dict[str, Any]]] = {
        "status": (Platform.SENSOR, {
            "name": "Status",
            "icon": "mdi:ev-station",
        }),
        "lock-state": (Platform.SENSOR, {
            "name": "Lock State",
            "icon": "mdi:lock",
        }),
        "advertised-current": (Platform.SENSOR, {
            "name": "Advertised Current",
            "device_class": SensorDeviceClass.CURRENT,
            "state_class": SensorStateClass.MEASUREMENT,
            "native_unit": UnitOfElectricCurrent.AMPERE,
        }),
        "vendor-name": (Platform.SENSOR, {
            "name": "Vendor",
            "entity_category": EntityCategory.DIAGNOSTIC,
        }),
        "product-name": (Platform.SENSOR, {
            "name": "Product",
            "entity_category": EntityCategory.DIAGNOSTIC,
        }),
        "part-number": (Platform.SENSOR, {
            "name": "Part Number",
            "entity_category": EntityCategory.DIAGNOSTIC,
        }),
        "serial-number": (Platform.SENSOR, {
            "name": "Serial Number",
            "entity_category": EntityCategory.DIAGNOSTIC,
        }),
        "software-version": (Platform.SENSOR, {
            "name": "Firmware Version",
            "entity_category": EntityCategory.DIAGNOSTIC,
        }),
        "feed": (Platform.SENSOR, {
            "name": "Feed Circuit",
            "entity_category": EntityCategory.DIAGNOSTIC,
        }),
    }

    for prop_id, prop_desc in properties.items():
        if prop_id in prop_map:
            platform, kwargs = prop_map[prop_id]
            specs.append(EntitySpec(
                platform=platform,
                node_id=node_id,
                property_id=prop_id,
                **kwargs,
            ))
        else:
            unit = prop_desc.get("unit", "")
            name = _humanize(prop_id)
            if unit in ("W", "kW") or "power" in prop_id:
                native_unit = UnitOfPower.KILO_WATT if unit == "kW" else UnitOfPower.WATT
                specs.append(EntitySpec(
                    platform=Platform.SENSOR,
                    node_id=node_id,
                    property_id=prop_id,
                    name=name,
                    device_class=SensorDeviceClass.POWER,
                    state_class=SensorStateClass.MEASUREMENT,
                    native_unit=native_unit,
                ))

    return specs


def _map_pcs_properties(
    node_id: str, properties: dict[str, Any]
) -> list[EntitySpec]:
    """Map Power Control System (PCS) node properties."""
    specs: list[EntitySpec] = []

    for prop_id, prop_desc in properties.items():
        datatype = prop_desc.get("datatype", "")
        unit = prop_desc.get("unit", "")
        name = _humanize(prop_id)

        if datatype == "boolean":
            specs.append(EntitySpec(
                platform=Platform.BINARY_SENSOR,
                node_id=node_id,
                property_id=prop_id,
                name=name,
                entity_category=EntityCategory.DIAGNOSTIC,
            ))
        elif unit == "A":
            specs.append(EntitySpec(
                platform=Platform.SENSOR,
                node_id=node_id,
                property_id=prop_id,
                name=name,
                device_class=SensorDeviceClass.CURRENT,
                state_class=SensorStateClass.MEASUREMENT,
                native_unit=UnitOfElectricCurrent.AMPERE,
                entity_category=EntityCategory.DIAGNOSTIC,
            ))
        elif datatype == "enum":
            specs.append(EntitySpec(
                platform=Platform.SENSOR,
                node_id=node_id,
                property_id=prop_id,
                name=name,
                entity_category=EntityCategory.DIAGNOSTIC,
            ))

    return specs


# ── Dispatcher ────────────────────────────────────────────────────────────

# Map Homie node type → mapper function
_NODE_TYPE_MAPPERS: dict[str, Any] = {
    "energy.ebus.device.distribution-enclosure.core": _map_core_properties,
    "energy.ebus.device.circuit": _map_circuit_properties,
    "energy.ebus.device.power-flows": _map_power_flow_properties,
    "energy.ebus.device.lugs.upstream": _map_lug_properties,
    "energy.ebus.device.lugs.downstream": _map_lug_properties,
    "energy.ebus.device.bess": _map_bess_properties,
    "energy.ebus.device.pv": _map_pv_properties,
    "energy.ebus.device.evse": _map_evse_properties,
    "energy.ebus.device.pcs": _map_pcs_properties,
}


def entities_from_description(
    description: dict[str, Any],
    panel: Any | None = None,
) -> list[EntitySpec]:
    """Parse a Homie $description and return entity specs for all nodes.

    Only creates entities for nodes and properties actually present
    in the panel's description — forward-compatible with new panel features.

    Sub-device node types (circuits, BESS, PV, EVSE) get ``node_type`` and
    ``device_name`` stamped on each returned spec so the entity base can
    create child devices via ``via_device``.

    Args:
        description: Parsed Homie $description JSON.
        panel: Optional SpanPanel instance for looking up runtime property
            values (e.g. circuit names).

    """
    from .util import _NODE_TYPE_LABELS, _SUB_DEVICE_TYPES  # noqa: PLC0415

    specs: list[EntitySpec] = []
    nodes = description.get("nodes", {})

    for node_id, node_desc in nodes.items():
        properties = node_desc.get("properties", {})
        if not properties:
            continue

        node_type = node_desc.get("type", "")
        mapper = _NODE_TYPE_MAPPERS.get(node_type)

        if mapper is None:
            _LOGGER.debug("No mapper for node %s (type=%s), skipping", node_id, node_type)
            continue

        new_specs = mapper(node_id, properties)

        # Stamp sub-device info so entities can create child devices
        if node_type in _SUB_DEVICE_TYPES:
            if node_type == "energy.ebus.device.circuit":
                circuit_name = None
                if panel is not None:
                    circuit_name = panel.get_property_value(node_id, "name")
                dev_name = circuit_name or f"Circuit {node_id[:6]}"
            else:
                type_label = _NODE_TYPE_LABELS.get(node_type, _humanize(node_id))
                if panel is not None:
                    short_serial = panel.serial_number.rsplit("-", 1)[-1]
                    dev_name = f"{short_serial} {type_label}"
                else:
                    dev_name = type_label

            for spec in new_specs:
                spec.node_type = node_type
                spec.device_name = dev_name

        specs.extend(new_specs)

    return specs


# ── Helpers ───────────────────────────────────────────────────────────────

def _parse_enum_format(fmt: str) -> list[str]:
    """Parse Homie enum format string ('val1,val2,val3') into options list."""
    if not fmt:
        return []
    return [v.strip() for v in fmt.split(",") if v.strip()]


def _humanize(prop_id: str) -> str:
    """Convert property-id to human-readable name.

    Handles known abbreviations (PV, EV, SOC, etc.).
    """
    parts = prop_id.replace("_", "-").split("-")
    return " ".join(_ABBREVIATIONS.get(p, p.title()) for p in parts)
