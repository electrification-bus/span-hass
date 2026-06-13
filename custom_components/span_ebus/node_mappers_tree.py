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
from homeassistant.components.sensor import SensorStateClass
from homeassistant.const import EntityCategory, Platform

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


def _map_enclosure_meter(
    device_id: str, capability: str, properties: dict[str, Any], device_data: dict[str, Any]
) -> list[EntitySpec]:
    """TODO Phase 2 — panel-level voltages (l1/l2) + lug-mirrored energies (skip mirrors)."""
    return []


def _map_enclosure_status(
    device_id: str, capability: str, properties: dict[str, Any], device_data: dict[str, Any]
) -> list[EntitySpec]:
    """TODO Phase 2 — network/cloud/relay status (was core/{ethernet,wifi,...})."""
    return []


def _map_enclosure_pcs(
    device_id: str, capability: str, properties: dict[str, Any], device_data: dict[str, Any]
) -> list[EntitySpec]:
    """TODO Phase 2 — Power Control System (grid-islandable, breaker-rating, limits)."""
    return []


def _map_enclosure_power_flows(
    device_id: str, capability: str, properties: dict[str, Any], device_data: dict[str, Any]
) -> list[EntitySpec]:
    """TODO Phase 2 — panel directional flow totals (pv/battery/grid/site)."""
    return []


def _map_enclosure_shed_forecast(
    device_id: str, capability: str, properties: dict[str, Any], device_data: dict[str, Any]
) -> list[EntitySpec]:
    """TODO Phase 2 — net-new shed-forecast capability (BTR time-remaining + confidence)."""
    return []


def _map_enclosure_shed(
    device_id: str, capability: str, properties: dict[str, Any], device_data: dict[str, Any]
) -> list[EntitySpec]:
    """TODO Phase 2 — net-new shed capability (override switch, soc-threshold)."""
    return []


# ─ Lugs (upstream + downstream) capabilities ─


def _map_lugs_info(
    device_id: str, capability: str, properties: dict[str, Any], device_data: dict[str, Any]
) -> list[EntitySpec]:
    """TODO Phase 2 — lugs info (direction)."""
    return []


def _map_lugs_meter(
    device_id: str, capability: str, properties: dict[str, Any], device_data: dict[str, Any]
) -> list[EntitySpec]:
    """TODO Phase 2 — lugs meter (currents, power, imported/exported energy)."""
    return []


def _map_lugs_connection(
    device_id: str, capability: str, properties: dict[str, Any], device_data: dict[str, Any]
) -> list[EntitySpec]:
    """TODO Phase 2 — lugs connection capability (fed-by-/feeds-device triplet)."""
    return []


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
