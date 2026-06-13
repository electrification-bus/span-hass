"""Tests for the tree-data-model node mappers.

Phase 1 coverage: the dispatcher walks a tree-v1 snapshot end-to-end, the two
fully-implemented mappers (``(distribution-enclosure, info)`` and
``(distribution-enclosure, door)``) produce correct EntitySpecs, and the 24
stubbed mappers don't crash. Phase 2 will add per-mapper coverage as those land.
"""

from __future__ import annotations

import json
from pathlib import Path
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
import pytest

from custom_components.span_ebus.const import (
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
    DEVICE_TYPE_DISTRIBUTION_ENCLOSURE,
)
from custom_components.span_ebus.node_mappers_tree import (
    CAPABILITY_MAPPERS,
    EntitySpec,
    _map_bess_info,
    _map_bess_soc,
    _map_circuit_connection,
    _map_circuit_info,
    _map_circuit_meter,
    _map_circuit_priority,
    _map_circuit_switch,
    _map_enclosure_door,
    _map_enclosure_info,
    _map_enclosure_meter,
    _map_enclosure_pcs,
    _map_enclosure_power_flows,
    _map_enclosure_shed,
    _map_enclosure_shed_forecast,
    _map_enclosure_status,
    _map_evse_config,
    _map_evse_info,
    _map_evse_meter,
    _map_evse_status,
    _map_evse_switch,
    _map_lugs_connection,
    _map_lugs_info,
    _map_lugs_meter,
    _map_mid_grid,
    _map_mid_info,
    _map_pv_info,
    _parse_enum_format,
    device_type_short,
    entities_from_tree,
)

FIXTURES = Path(__file__).parent / "fixtures" / "tree"


def _load(name: str) -> dict[str, Any]:
    """Load a tree-v1 snapshot fixture and return its ``devices`` map."""
    with (FIXTURES / name).open() as fh:
        data = json.load(fh)
    assert data["metadata"]["schema"] == "tree-v1"
    return data["devices"]


# ── device_type_short ─────────────────────────────────────────────────────


def test_device_type_short_strips_prefix() -> None:
    assert device_type_short("energy.ebus.device.distribution-enclosure") == "distribution-enclosure"
    assert device_type_short("energy.ebus.device.bess") == "bess"
    assert device_type_short("energy.ebus.device.mid") == "mid"


def test_device_type_short_rejects_non_ebus() -> None:
    assert device_type_short("homie.device.thermostat") is None
    assert device_type_short("") is None


# ── _map_enclosure_info ───────────────────────────────────────────────────


def test_map_enclosure_info_emits_one_spec_per_declared_property() -> None:
    properties = {
        "vendor-name": {"datatype": "string"},
        "model": {"datatype": "enum", "format": "MAIN_32"},
        "serial-number": {"datatype": "string"},
        "hardware-version": {"datatype": "string"},
        "firmware-version": {"datatype": "string"},
        "data-model-version": {"datatype": "string"},
    }
    specs = _map_enclosure_info("nt-test-abc12", CAPABILITY_INFO, properties, {})
    assert len(specs) == 6
    for spec in specs:
        assert spec.device_id == "nt-test-abc12"
        assert spec.capability == CAPABILITY_INFO
        assert spec.platform == Platform.SENSOR
        assert spec.entity_category == EntityCategory.DIAGNOSTIC

    property_ids = {s.property_id for s in specs}
    assert property_ids == {
        "vendor-name",
        "model",
        "serial-number",
        "hardware-version",
        "firmware-version",
        "data-model-version",
    }


def test_map_enclosure_info_handles_legacy_software_version() -> None:
    """Snapshots predating the firmware-side rename still publish software-version."""
    properties = {
        "vendor-name": {"datatype": "string"},
        "software-version": {"datatype": "string"},
        "data-model-version": {"datatype": "string"},
    }
    specs = _map_enclosure_info("nt-test-abc12", CAPABILITY_INFO, properties, {})
    versions = [s for s in specs if s.property_id == "software-version"]
    assert len(versions) == 1
    assert versions[0].name == "Firmware Version"


def test_map_enclosure_info_skips_undeclared_properties() -> None:
    """Mapper only emits specs for properties actually present in the description."""
    specs = _map_enclosure_info("nt-test-abc12", CAPABILITY_INFO, {"vendor-name": {}}, {})
    assert len(specs) == 1
    assert specs[0].property_id == "vendor-name"


# ── _map_enclosure_door ───────────────────────────────────────────────────


def test_map_enclosure_door_emits_tamper_binary_sensor() -> None:
    specs = _map_enclosure_door(
        "nt-test-abc12", CAPABILITY_DOOR, {"state": {"datatype": "enum"}}, {}
    )
    assert len(specs) == 1
    spec = specs[0]
    assert spec.device_id == "nt-test-abc12"
    assert spec.capability == CAPABILITY_DOOR
    assert spec.property_id == "state"
    assert spec.platform == Platform.BINARY_SENSOR
    assert spec.device_class == BinarySensorDeviceClass.TAMPER
    assert spec.on_values == {"OPEN"}


def test_map_enclosure_door_falls_back_to_legacy_property_name() -> None:
    """Snapshots predating the firmware-side rename still publish door/door."""
    specs = _map_enclosure_door(
        "nt-test-abc12", CAPABILITY_DOOR, {"door": {"datatype": "enum"}}, {}
    )
    assert len(specs) == 1
    assert specs[0].property_id == "door"


def test_map_enclosure_door_prefers_state_over_legacy() -> None:
    """If both are somehow published, the spec-current name wins."""
    specs = _map_enclosure_door(
        "nt-test-abc12",
        CAPABILITY_DOOR,
        {"state": {"datatype": "enum"}, "door": {"datatype": "enum"}},
        {},
    )
    assert len(specs) == 1
    assert specs[0].property_id == "state"


def test_map_enclosure_door_emits_nothing_when_absent() -> None:
    assert _map_enclosure_door("nt-test-abc12", CAPABILITY_DOOR, {}, {}) == []


# ── _map_enclosure_meter ──────────────────────────────────────────────────


def test_map_enclosure_meter_emits_l1_l2_voltage() -> None:
    specs = _map_enclosure_meter(
        "panel-1",
        CAPABILITY_METER,
        {
            "l1-voltage": {"datatype": "float", "unit": "V"},
            "l2-voltage": {"datatype": "float", "unit": "V"},
        },
        {},
    )
    assert len(specs) == 2
    for spec in specs:
        assert spec.platform == Platform.SENSOR
        assert spec.device_class == SensorDeviceClass.VOLTAGE
        assert spec.state_class == SensorStateClass.MEASUREMENT
        assert spec.native_unit == UnitOfElectricPotential.VOLT


def test_map_enclosure_meter_ignores_internal_mirrors() -> None:
    """l1-current etc. live on lugs; if a future firmware mirrors them, do not duplicate."""
    specs = _map_enclosure_meter(
        "panel-1",
        CAPABILITY_METER,
        {"l1-current": {}, "active-power": {}, "imported-energy": {}},
        {},
    )
    assert specs == []


# ── _map_enclosure_status ─────────────────────────────────────────────────


def test_map_enclosure_status_relay_is_binary_with_closed_as_on() -> None:
    specs = _map_enclosure_status(
        "panel-1",
        CAPABILITY_STATUS,
        {"relay": {"datatype": "enum", "format": "UNKNOWN,OPEN,CLOSED"}},
        {},
    )
    assert len(specs) == 1
    assert specs[0].platform == Platform.BINARY_SENSOR
    assert specs[0].on_values == {"CLOSED"}


def test_map_enclosure_status_emits_seven_specs_from_panel_a_shape() -> None:
    """panel_a publishes all 7 status properties (with legacy vendor-cloud name)."""
    specs = _map_enclosure_status(
        "panel-1",
        CAPABILITY_STATUS,
        {
            "relay": {},
            "ethernet": {},
            "wifi": {},
            "wifi-ssid": {},
            "vendor-cloud": {},
            "postal-code": {},
            "time-zone": {},
        },
        {},
    )
    assert len(specs) == 7
    cloud = [s for s in specs if s.name == "Cloud Connection"]
    assert len(cloud) == 1
    assert cloud[0].property_id == "vendor-cloud"


def test_map_enclosure_status_uses_spec_cloud_connection_name_when_published() -> None:
    specs = _map_enclosure_status(
        "panel-1",
        CAPABILITY_STATUS,
        {"cloud-connection": {}},
        {},
    )
    assert len(specs) == 1
    assert specs[0].property_id == "cloud-connection"
    assert specs[0].name == "Cloud Connection"


# ── _map_enclosure_pcs ────────────────────────────────────────────────────


def test_map_enclosure_pcs_emits_17_specs_for_panel_a() -> None:
    """panel_a publishes the full PCS surface: 4 fixed + 5 limit families × 3 = 17."""
    properties = {
        "enabled": {}, "active": {},
        "grid-islandable": {}, "breaker-rating": {"unit": "A"},
        "import-limit": {"unit": "A"},
        "import-limit-enablement": {},
        "import-limit-active": {},
        "feed-import-limit": {"unit": "A"},
        "feed-import-limit-enablement": {},
        "feed-import-limit-active": {},
        "grid-import-limit": {"unit": "A"},
        "grid-import-limit-enablement": {},
        "grid-import-limit-active": {},
        "off-grid-import-limit": {"unit": "A"},
        "off-grid-import-limit-enablement": {},
        "off-grid-import-limit-active": {},
        "requested-import-limit": {"unit": "A"},
        "requested-import-limit-enablement": {},
        "requested-import-limit-active": {},
    }
    specs = _map_enclosure_pcs("panel-1", CAPABILITY_PCS, properties, {})
    assert len(specs) == 19  # 4 + (5 × 3) = 19

    limit_sensors = [s for s in specs if s.device_class == SensorDeviceClass.CURRENT and s.property_id != "breaker-rating"]
    assert len(limit_sensors) == 5
    for s in limit_sensors:
        assert s.native_unit == UnitOfElectricCurrent.AMPERE
        assert s.state_class == SensorStateClass.MEASUREMENT


def test_map_enclosure_pcs_breaker_rating_is_diagnostic_current() -> None:
    specs = _map_enclosure_pcs(
        "panel-1", CAPABILITY_PCS, {"breaker-rating": {"unit": "A"}}, {}
    )
    assert len(specs) == 1
    assert specs[0].device_class == SensorDeviceClass.CURRENT
    assert specs[0].entity_category == EntityCategory.DIAGNOSTIC
    assert specs[0].state_class is None


# ── _map_enclosure_power_flows ────────────────────────────────────────────


def test_map_enclosure_power_flows_emits_four_directional_sensors() -> None:
    specs = _map_enclosure_power_flows(
        "panel-1",
        CAPABILITY_POWER_FLOWS,
        {kind: {"unit": "W"} for kind in ("pv", "battery", "grid", "site")},
        {},
    )
    assert len(specs) == 4
    for spec in specs:
        assert spec.platform == Platform.SENSOR
        assert spec.device_class == SensorDeviceClass.POWER
        assert spec.state_class == SensorStateClass.MEASUREMENT
        assert spec.native_unit == UnitOfPower.WATT


def test_map_enclosure_power_flows_pv_name_capitalization() -> None:
    """The pv flow's user-facing label should read 'PV Power', not 'Pv Power'."""
    specs = _map_enclosure_power_flows(
        "panel-1", CAPABILITY_POWER_FLOWS, {"pv": {"unit": "W"}}, {}
    )
    assert specs[0].name == "PV Power"


# ── _map_enclosure_shed_forecast ──────────────────────────────────────────


def test_map_enclosure_shed_forecast_emits_four_durations_and_confidence() -> None:
    specs = _map_enclosure_shed_forecast(
        "panel-1",
        CAPABILITY_SHED_FORECAST,
        {
            "total-time-remaining": {"unit": "min"},
            "time-to-priority-shed": {"unit": "min"},
            "full-charge-total-time-remaining": {"unit": "min"},
            "full-charge-time-to-priority-shed": {"unit": "min"},
            "confidence": {"datatype": "enum"},
        },
        {},
    )
    assert len(specs) == 5
    durations = [s for s in specs if s.device_class == SensorDeviceClass.DURATION]
    assert len(durations) == 4
    for d in durations:
        assert d.native_unit == UnitOfTime.MINUTES
    confidence = [s for s in specs if s.property_id == "confidence"]
    assert len(confidence) == 1
    assert confidence[0].entity_category == EntityCategory.DIAGNOSTIC


# ── _map_enclosure_shed ───────────────────────────────────────────────────


def test_map_enclosure_shed_override_is_settable_switch() -> None:
    specs = _map_enclosure_shed(
        "panel-1", CAPABILITY_SHED, {"override": {"datatype": "boolean", "settable": True}}, {}
    )
    assert len(specs) == 1
    assert specs[0].platform == Platform.SWITCH
    assert specs[0].settable is True


def test_map_enclosure_shed_soc_threshold_is_battery_percentage() -> None:
    specs = _map_enclosure_shed(
        "panel-1",
        CAPABILITY_SHED,
        {"soc-threshold": {"datatype": "integer", "unit": "%"}},
        {},
    )
    assert len(specs) == 1
    assert specs[0].device_class == SensorDeviceClass.BATTERY
    assert specs[0].native_unit == PERCENTAGE
    assert specs[0].entity_category == EntityCategory.DIAGNOSTIC


# ── _map_lugs_info ────────────────────────────────────────────────────────


def test_map_lugs_info_emits_direction_sensor() -> None:
    specs = _map_lugs_info(
        "panel-1-lugs-up",
        CAPABILITY_INFO,
        {"direction": {"datatype": "enum", "format": "UPSTREAM,DOWNSTREAM"}},
        {},
    )
    assert len(specs) == 1
    assert specs[0].platform == Platform.SENSOR
    assert specs[0].entity_category == EntityCategory.DIAGNOSTIC
    assert specs[0].name == "Direction"


def test_map_lugs_info_empty_when_no_direction() -> None:
    assert _map_lugs_info("panel-1-lugs-up", CAPABILITY_INFO, {}, {}) == []


# ── _map_lugs_meter ───────────────────────────────────────────────────────


_LUGS_METER_PROPERTIES = {
    "l1-current": {"unit": "A"},
    "l2-current": {"unit": "A"},
    "active-power": {"unit": "W"},
    "imported-energy": {"unit": "Wh"},
    "exported-energy": {"unit": "Wh"},
}


def test_map_lugs_meter_upstream_uses_grid_friendly_energy_names() -> None:
    device_data = {"properties": {"info/direction": "upstream"}}
    specs = _map_lugs_meter(
        "panel-1-lugs-up", CAPABILITY_METER, _LUGS_METER_PROPERTIES, device_data
    )
    assert len(specs) == 5
    by_id = {s.property_id: s for s in specs}
    assert by_id["imported-energy"].name == "Energy"
    assert by_id["exported-energy"].name == "Energy Returned"
    assert by_id["imported-energy"].device_class == SensorDeviceClass.ENERGY
    assert by_id["imported-energy"].state_class == SensorStateClass.TOTAL_INCREASING
    assert by_id["imported-energy"].native_unit == UnitOfEnergy.WATT_HOUR
    assert by_id["active-power"].device_class == SensorDeviceClass.POWER
    assert by_id["active-power"].native_unit == UnitOfPower.WATT
    assert by_id["l1-current"].native_unit == UnitOfElectricCurrent.AMPERE


def test_map_lugs_meter_downstream_uses_literal_energy_names() -> None:
    """Downstream-direction energy entities use unambiguous literal names.

    SPAN does not populate downstream lug values today; the literal names will
    read correctly when a future firmware enables inter-panel feedthrough.
    """
    device_data = {"properties": {"info/direction": "downstream"}}
    specs = _map_lugs_meter(
        "panel-1-lugs-dn", CAPABILITY_METER, _LUGS_METER_PROPERTIES, device_data
    )
    by_id = {s.property_id: s for s in specs}
    assert by_id["imported-energy"].name == "Imported Energy"
    assert by_id["exported-energy"].name == "Exported Energy"


def test_map_lugs_meter_unknown_direction_falls_back_to_literal() -> None:
    """Unknown direction still emits sensors with literal names.

    Tests the descriptor in isolation (when the publisher hasn't yet sent
    info/direction at the time entities_from_tree was called).
    """
    specs = _map_lugs_meter("panel-1-lugs-up", CAPABILITY_METER, _LUGS_METER_PROPERTIES, {})
    by_id = {s.property_id: s for s in specs}
    assert by_id["imported-energy"].name == "Imported Energy"
    assert by_id["exported-energy"].name == "Exported Energy"


# ── _map_lugs_connection ──────────────────────────────────────────────────


_LUGS_CONNECTION_FULL = {
    "fed-by-device-id": {"datatype": "string"},
    "fed-by-device-type": {"datatype": "string"},
    "fed-by-device-status": {"datatype": "enum", "format": "OK,LOST,DEGRADED"},
    "feeds-device-id": {"datatype": "string"},
    "feeds-device-type": {"datatype": "string"},
    "feeds-device-status": {"datatype": "enum", "format": "OK,LOST,DEGRADED"},
    "count": {"datatype": "integer"},
}


def test_map_lugs_connection_upstream_emits_fed_by_triplet_plus_count() -> None:
    device_data = {"properties": {"info/direction": "upstream"}}
    specs = _map_lugs_connection(
        "panel-1-lugs-up", CAPABILITY_CONNECTION, _LUGS_CONNECTION_FULL, device_data
    )
    prop_ids = {s.property_id for s in specs}
    assert prop_ids == {"fed-by-device-id", "fed-by-device-type", "fed-by-device-status", "count"}
    status = next(s for s in specs if s.property_id == "fed-by-device-status")
    assert status.platform == Platform.BINARY_SENSOR
    assert status.device_class == BinarySensorDeviceClass.PROBLEM
    assert status.on_values == {"LOST", "DEGRADED"}


def test_map_lugs_connection_downstream_emits_feeds_triplet_plus_count() -> None:
    device_data = {"properties": {"info/direction": "downstream"}}
    specs = _map_lugs_connection(
        "panel-1-lugs-dn", CAPABILITY_CONNECTION, _LUGS_CONNECTION_FULL, device_data
    )
    prop_ids = {s.property_id for s in specs}
    assert prop_ids == {"feeds-device-id", "feeds-device-type", "feeds-device-status", "count"}


def test_map_lugs_connection_unknown_direction_emits_nothing() -> None:
    """Unknown direction emits nothing to avoid permanently-empty entities.

    Without direction we cannot say which half (fed-by-* vs feeds-*) is real.
    """
    assert _map_lugs_connection("panel-1-lugs-up", CAPABILITY_CONNECTION, _LUGS_CONNECTION_FULL, {}) == []


# ── _map_bess_info ────────────────────────────────────────────────────────


_BESS_INFO_PROPERTIES_LEGACY_FIRMWARE = {
    "vendor-name": {"datatype": "string"},
    "product-name": {"datatype": "string"},
    "model": {"datatype": "string"},
    "serial-number": {"datatype": "string"},
    "software-version": {"datatype": "string"},
    "nameplate-capacity": {"datatype": "float", "unit": "kWh"},
}


def test_map_bess_info_emits_six_diagnostic_specs_for_legacy_publisher() -> None:
    """Snapshot fixtures still ship software-version (pre-rename); expect 6 specs."""
    specs = _map_bess_info("bess-1", CAPABILITY_INFO, _BESS_INFO_PROPERTIES_LEGACY_FIRMWARE, {})
    assert len(specs) == 6
    by_id = {s.property_id: s for s in specs}
    for prop_id in _BESS_INFO_PROPERTIES_LEGACY_FIRMWARE:
        assert prop_id in by_id
        assert by_id[prop_id].entity_category == EntityCategory.DIAGNOSTIC
    # software-version surfaces under the spec-current "Firmware Version" label.
    assert by_id["software-version"].name == "Firmware Version"
    cap = by_id["nameplate-capacity"]
    assert cap.device_class == SensorDeviceClass.ENERGY_STORAGE
    assert cap.native_unit == UnitOfEnergy.KILO_WATT_HOUR
    assert cap.state_class is None


def test_map_bess_info_picks_up_firmware_version_when_publisher_renames() -> None:
    specs = _map_bess_info(
        "bess-1",
        CAPABILITY_INFO,
        {"vendor-name": {}, "firmware-version": {}},
        {},
    )
    by_id = {s.property_id: s for s in specs}
    assert "firmware-version" in by_id
    assert by_id["firmware-version"].name == "Firmware Version"


# ── _map_bess_soc ─────────────────────────────────────────────────────────


def test_map_bess_soc_emits_battery_and_energy_storage_measurements() -> None:
    specs = _map_bess_soc(
        "bess-1",
        CAPABILITY_SOC,
        {
            "soc": {"datatype": "float", "unit": "%"},
            "soe": {"datatype": "float", "unit": "kWh"},
        },
        {},
    )
    by_id = {s.property_id: s for s in specs}
    assert by_id["soc"].device_class == SensorDeviceClass.BATTERY
    assert by_id["soc"].native_unit == PERCENTAGE
    assert by_id["soc"].state_class == SensorStateClass.MEASUREMENT
    assert by_id["soe"].device_class == SensorDeviceClass.ENERGY_STORAGE
    assert by_id["soe"].native_unit == UnitOfEnergy.KILO_WATT_HOUR
    assert by_id["soe"].state_class == SensorStateClass.MEASUREMENT


# ── _map_mid_info ─────────────────────────────────────────────────────────


def test_map_mid_info_emits_six_diagnostic_specs() -> None:
    """MID snapshot already uses the spec firmware-version name."""
    specs = _map_mid_info(
        "bess-1-mid",
        CAPABILITY_INFO,
        {
            "vendor-name": {},
            "product-name": {},
            "model": {},
            "serial-number": {},
            "hardware-version": {},
            "firmware-version": {},
        },
        {},
    )
    assert len(specs) == 6
    for s in specs:
        assert s.entity_category == EntityCategory.DIAGNOSTIC


# ── _map_mid_grid ─────────────────────────────────────────────────────────


def test_map_mid_grid_islanding_and_grid_state_keep_full_enum_value() -> None:
    """All three properties emit text sensors.

    DEGRADED (vs DOWN/UNKNOWN) doesn't collapse into a single 'not ok' bit,
    the way a PROBLEM binary_sensor would.
    """
    specs = _map_mid_grid(
        "bess-1-mid",
        CAPABILITY_GRID,
        {
            "islanding-state": {"datatype": "enum", "format": "ON_GRID,OFF_GRID,UNKNOWN"},
            "grid-state": {"datatype": "enum", "format": "UP,DOWN,DEGRADED,UNKNOWN"},
            "grid-forming-entity": {"datatype": "string"},
        },
        {},
    )
    by_id = {s.property_id: s for s in specs}
    assert by_id["islanding-state"].platform == Platform.SENSOR
    assert by_id["grid-state"].platform == Platform.SENSOR
    assert by_id["grid-forming-entity"].entity_category == EntityCategory.DIAGNOSTIC
    # islanding-state and grid-state are operational, not diagnostic.
    assert by_id["islanding-state"].entity_category is None
    assert by_id["grid-state"].entity_category is None


# ── _map_pv_info ──────────────────────────────────────────────────────────


def test_map_pv_info_emits_five_specs_with_watts_for_nameplate() -> None:
    """Tree-v1 publishes nameplate-capacity in watts.

    The legacy kW-but-actually-W firmware bug is fixed in the new model.
    """
    specs = _map_pv_info(
        "pv-1",
        CAPABILITY_INFO,
        {
            "vendor-name": {},
            "product-name": {},
            "serial-number": {},
            "software-version": {},
            "nameplate-capacity": {"unit": "W"},
        },
        {},
    )
    assert len(specs) == 5
    by_id = {s.property_id: s for s in specs}
    cap = by_id["nameplate-capacity"]
    assert cap.device_class == SensorDeviceClass.POWER
    assert cap.native_unit == UnitOfPower.WATT


def test_map_pv_info_no_model_or_hardware_version_in_v1() -> None:
    """PV info has no model / hardware-version rows on the wire.

    If a future firmware adds them the mapper just keeps emitting what's
    declared elsewhere.
    """
    specs = _map_pv_info(
        "pv-1",
        CAPABILITY_INFO,
        {"model": {}, "hardware-version": {}},
        {},
    )
    # Mapper has no row for these keys, so they're silently skipped.
    assert specs == []


# ── EVSE mappers (no fixture coverage — neither snapshot has a Drive) ───


def test_map_evse_info_emits_five_diagnostic_specs_for_legacy_publisher() -> None:
    """EVSE info has 5 fields including the EVSE-unique part-number."""
    specs = _map_evse_info(
        "evse-1",
        CAPABILITY_INFO,
        {
            "vendor-name": {},
            "product-name": {},
            "part-number": {},
            "serial-number": {},
            "software-version": {},
        },
        {},
    )
    assert len(specs) == 5
    by_id = {s.property_id: s for s in specs}
    assert by_id["part-number"].name == "Part Number"
    assert by_id["software-version"].name == "Firmware Version"
    for s in specs:
        assert s.entity_category == EntityCategory.DIAGNOSTIC


def test_map_evse_info_no_model_row() -> None:
    """EVSE info has no model row on the wire — the mapper silently skips it."""
    specs = _map_evse_info("evse-1", CAPABILITY_INFO, {"model": {}}, {})
    assert specs == []


def test_map_evse_status_emits_text_sensor_with_ev_icon() -> None:
    specs = _map_evse_status(
        "evse-1",
        CAPABILITY_STATUS,
        {"operational-state": {"datatype": "enum"}},
        {},
    )
    assert len(specs) == 1
    spec = specs[0]
    assert spec.platform == Platform.SENSOR
    assert spec.icon == "mdi:ev-station"
    assert spec.name == "Status"


def test_map_evse_switch_emits_text_lock_state_sensor() -> None:
    """Lock state is read-only per spec; surface as text sensor not a switch."""
    specs = _map_evse_switch(
        "evse-1",
        CAPABILITY_SWITCH,
        {"lock-state": {"datatype": "enum"}},
        {},
    )
    assert len(specs) == 1
    spec = specs[0]
    assert spec.platform == Platform.SENSOR
    assert spec.settable is False
    assert spec.icon == "mdi:lock"


def test_map_evse_meter_emits_current_measurement() -> None:
    specs = _map_evse_meter(
        "evse-1",
        CAPABILITY_METER,
        {"advertised-current": {"datatype": "float", "unit": "A"}},
        {},
    )
    assert len(specs) == 1
    spec = specs[0]
    assert spec.device_class == SensorDeviceClass.CURRENT
    assert spec.state_class == SensorStateClass.MEASUREMENT
    assert spec.native_unit == UnitOfElectricCurrent.AMPERE


def test_map_evse_config_emits_two_current_sensors() -> None:
    """user-max-charge-current and max-charge-current both surface as CURRENT diagnostics.

    Phase 3 will upgrade user-max-charge-current to Platform.NUMBER for
    settability; for now it's a read-only sensor.
    """
    specs = _map_evse_config(
        "evse-1",
        CAPABILITY_CONFIG,
        {
            "user-max-charge-current": {"unit": "A", "settable": True},
            "max-charge-current": {"unit": "A"},
        },
        {},
    )
    assert len(specs) == 2
    by_id = {s.property_id: s for s in specs}
    for prop_id in ("user-max-charge-current", "max-charge-current"):
        s = by_id[prop_id]
        assert s.device_class == SensorDeviceClass.CURRENT
        assert s.native_unit == UnitOfElectricCurrent.AMPERE
        assert s.entity_category == EntityCategory.DIAGNOSTIC


# ── _map_circuit_info ─────────────────────────────────────────────────────


def test_map_circuit_info_maps_legacy_space_under_tab_number_name() -> None:
    """Snapshot fixtures still publish ``space`` (pre-rename); surface as Tab Number."""
    specs = _map_circuit_info(
        "circ-1",
        CAPABILITY_INFO,
        {
            "name": {"datatype": "string"},
            "breaker-rating": {"datatype": "integer", "unit": "A"},
            "space": {"datatype": "integer"},
            "dipole": {"datatype": "boolean"},
        },
        {},
    )
    by_id = {s.property_id: s for s in specs}
    assert by_id["space"].name == "Tab Number"
    assert by_id["breaker-rating"].device_class == SensorDeviceClass.CURRENT
    assert by_id["dipole"].platform == Platform.BINARY_SENSOR
    assert by_id["name"].entity_category == EntityCategory.DIAGNOSTIC


def test_map_circuit_info_picks_up_tab_number_after_rename() -> None:
    specs = _map_circuit_info(
        "circ-1", CAPABILITY_INFO, {"tab-number": {"datatype": "integer"}}, {}
    )
    assert len(specs) == 1
    assert specs[0].name == "Tab Number"


# ── _map_circuit_meter ────────────────────────────────────────────────────


_CIRCUIT_METER_PROPERTIES = {
    "current": {"unit": "A"},
    "active-power": {"unit": "W"},
    "imported-energy": {"unit": "Wh"},
    "exported-energy": {"unit": "Wh"},
}


def test_map_circuit_meter_emits_four_specs() -> None:
    specs = _map_circuit_meter("circ-1", CAPABILITY_METER, _CIRCUIT_METER_PROPERTIES, {})
    assert len(specs) == 4
    by_id = {s.property_id: s for s in specs}
    # Power is W with sign-flip to match HA's positive-consumption convention.
    assert by_id["active-power"].native_unit == UnitOfPower.WATT
    assert by_id["active-power"].negate is True
    # SPAN's panel-perspective: exported-energy = consumption ("Energy", dominant counter);
    # imported-energy = backfeed ("Energy Returned", typically ~0 on a load circuit).
    assert by_id["exported-energy"].name == "Energy"
    assert by_id["imported-energy"].name == "Energy Returned"
    # Both energies are TOTAL_INCREASING (monotonicity workaround per AN-001 lives
    # at a layer below the mapper).
    for prop_id in ("imported-energy", "exported-energy"):
        s = by_id[prop_id]
        assert s.device_class == SensorDeviceClass.ENERGY
        assert s.state_class == SensorStateClass.TOTAL_INCREASING
        assert s.native_unit == UnitOfEnergy.WATT_HOUR


# ── _map_circuit_switch ───────────────────────────────────────────────────


def test_map_circuit_switch_relay_is_settable_when_relay_controllable_true() -> None:
    specs = _map_circuit_switch(
        "circ-1",
        CAPABILITY_SWITCH,
        {"relay": {"datatype": "enum", "format": "UNKNOWN,OPEN,CLOSED", "settable": True}},
        {"properties": {"priority/relay-controllable": True}},
    )
    relay = next(s for s in specs if s.property_id == "relay")
    assert relay.platform == Platform.SWITCH
    assert relay.settable is True


def test_map_circuit_switch_relay_locked_when_relay_controllable_false() -> None:
    """A non-controllable circuit surfaces relay as a non-settable switch.

    Always-on / always-off circuits have relay-controllable=False; the spec
    gates $settable accordingly and the entity should match.
    """
    specs = _map_circuit_switch(
        "circ-1",
        CAPABILITY_SWITCH,
        {"relay": {"datatype": "enum", "format": "UNKNOWN,OPEN,CLOSED"}},
        {"properties": {"priority/relay-controllable": False}},
    )
    relay = next(s for s in specs if s.property_id == "relay")
    assert relay.settable is False


def test_map_circuit_switch_relay_defaults_settable_when_unknown() -> None:
    """Unknown relay-controllable defaults to settable=True.

    The publisher will refuse out-of-condition writes anyway, so this is safe.
    """
    specs = _map_circuit_switch(
        "circ-1",
        CAPABILITY_SWITCH,
        {"relay": {"datatype": "enum", "format": "UNKNOWN,OPEN,CLOSED"}},
        {},
    )
    relay = next(s for s in specs if s.property_id == "relay")
    assert relay.settable is True


def test_map_circuit_switch_relay_requester_is_diagnostic_text() -> None:
    specs = _map_circuit_switch(
        "circ-1",
        CAPABILITY_SWITCH,
        {"relay-requester": {"datatype": "enum", "format": "USER,LOAD_SHED,PCS,FAULT"}},
        {},
    )
    assert len(specs) == 1
    assert specs[0].platform == Platform.SENSOR
    assert specs[0].entity_category == EntityCategory.DIAGNOSTIC


# ── _map_circuit_priority ─────────────────────────────────────────────────


_CIRCUIT_PRIORITY_PROPERTIES = {
    "shed-priority": {
        "datatype": "enum",
        "format": "UNKNOWN,OFF_GRID,SOC_THRESHOLD,NEVER",
        "settable": True,
    },
    "pcs-managed": {"datatype": "boolean"},
    "pcs-priority": {"datatype": "integer"},
    "relay-controllable": {"datatype": "boolean"},
}


def test_map_circuit_priority_emits_four_specs() -> None:
    specs = _map_circuit_priority(
        "circ-1", CAPABILITY_PRIORITY, _CIRCUIT_PRIORITY_PROPERTIES, {}
    )
    assert len(specs) == 4
    by_id = {s.property_id: s for s in specs}
    shed = by_id["shed-priority"]
    assert shed.platform == Platform.SELECT
    assert shed.settable is True
    assert shed.options == ["UNKNOWN", "OFF_GRID", "SOC_THRESHOLD", "NEVER"]
    assert by_id["pcs-managed"].platform == Platform.BINARY_SENSOR
    assert by_id["pcs-priority"].entity_category == EntityCategory.DIAGNOSTIC
    assert by_id["relay-controllable"].platform == Platform.BINARY_SENSOR


def test_map_circuit_priority_shed_priority_locked_when_internal_flag_false() -> None:
    """Publisher-derived shed-priority-settable=False surfaces as a non-settable select.

    Indicates the circuit is commissioned as permanent OFF_GRID.
    """
    specs = _map_circuit_priority(
        "circ-1",
        CAPABILITY_PRIORITY,
        _CIRCUIT_PRIORITY_PROPERTIES,
        {"properties": {"priority/shed-priority-settable": False}},
    )
    shed = next(s for s in specs if s.property_id == "shed-priority")
    assert shed.settable is False


# ── _map_circuit_connection ───────────────────────────────────────────────


def test_map_circuit_connection_feeds_status_is_problem_binary() -> None:
    specs = _map_circuit_connection(
        "circ-1",
        CAPABILITY_CONNECTION,
        {
            "feeds-device-id": {"datatype": "string"},
            "feeds-device-type": {"datatype": "string"},
            "feeds-device-status": {"datatype": "enum", "format": "OK,LOST,DEGRADED"},
            "count": {"datatype": "integer"},
        },
        {},
    )
    by_id = {s.property_id: s for s in specs}
    status = by_id["feeds-device-status"]
    assert status.platform == Platform.BINARY_SENSOR
    assert status.device_class == BinarySensorDeviceClass.PROBLEM
    assert status.on_values == {"LOST", "DEGRADED"}
    # Non-status feeds are surfaced as diagnostic text sensors.
    for prop_id in ("feeds-device-id", "feeds-device-type", "count"):
        assert by_id[prop_id].platform == Platform.SENSOR
        assert by_id[prop_id].entity_category == EntityCategory.DIAGNOSTIC


# ── _parse_enum_format ────────────────────────────────────────────────────


def test_parse_enum_format_splits_and_strips() -> None:
    assert _parse_enum_format("A,B, C ,D") == ["A", "B", "C", "D"]
    assert _parse_enum_format("") == []
    assert _parse_enum_format("A,,B") == ["A", "B"]


# ── Dispatch table ────────────────────────────────────────────────────────


def test_dispatch_table_covers_26_capabilities() -> None:
    """Every (device-class, capability) pair from the design doc has a mapper."""
    assert len(CAPABILITY_MAPPERS) == 26


def test_dispatch_table_includes_distribution_enclosure_info_and_door() -> None:
    assert CAPABILITY_MAPPERS[(DEVICE_TYPE_DISTRIBUTION_ENCLOSURE, CAPABILITY_INFO)] is _map_enclosure_info
    assert CAPABILITY_MAPPERS[(DEVICE_TYPE_DISTRIBUTION_ENCLOSURE, CAPABILITY_DOOR)] is _map_enclosure_door


# ── entities_from_tree against real snapshots ────────────────────────────


@pytest.fixture
def panel_a_devices() -> dict[str, Any]:
    return _load("nt-0000-abc12.json")


@pytest.fixture
def panel_b_devices() -> dict[str, Any]:
    return _load("nt-0000-def34.json")


def _by_device_capability(specs: list[EntitySpec]) -> dict[tuple[str, str], int]:
    """Group spec counts by (device_id, capability) for tabular assertions."""
    counts: dict[tuple[str, str], int] = {}
    for s in specs:
        key = (s.device_id, s.capability)
        counts[key] = counts.get(key, 0) + 1
    return counts


def test_walk_panel_a_snapshot_produces_full_entity_set(panel_a_devices: dict[str, Any]) -> None:
    """Walk panel_a end-to-end with all 26 mappers implemented.

    panel_a has 11 circuits; each contributes 18 specs (info 4 + meter 4 +
    switch 2 + priority 4 + connection 4). Total = 81 (non-circuit) + 198 = 279.
    """
    specs = entities_from_tree(panel_a_devices)
    counts = _by_device_capability(specs)

    panel = "nt-0000-abc12"
    lugs_up = "nt-0000-abc12-lugs-up"
    lugs_dn = "nt-0000-abc12-lugs-dn"
    bess = "nt-0000-abc12-bess0001"
    mid = "nt-0000-abc12-bess0001-mid"

    # Panel root — 8 enclosure capabilities = 44 specs (see Phase 2.1).
    assert counts[(panel, CAPABILITY_INFO)] == 6
    assert counts[(panel, CAPABILITY_DOOR)] == 1
    assert counts[(panel, CAPABILITY_METER)] == 2
    assert counts[(panel, CAPABILITY_STATUS)] == 7
    assert counts[(panel, CAPABILITY_PCS)] == 17
    assert counts[(panel, CAPABILITY_POWER_FLOWS)] == 4
    assert counts[(panel, CAPABILITY_SHED_FORECAST)] == 5
    assert counts[(panel, CAPABILITY_SHED)] == 2

    # Upstream lugs: info(1) + meter(5) + connection(4 — count + 3 fed-by-*) = 10.
    assert counts[(lugs_up, CAPABILITY_INFO)] == 1
    assert counts[(lugs_up, CAPABILITY_METER)] == 5
    assert counts[(lugs_up, CAPABILITY_CONNECTION)] == 4

    # Downstream lugs: info(1) + meter(5) + connection(4 — count + 3 feeds-*) = 10.
    assert counts[(lugs_dn, CAPABILITY_INFO)] == 1
    assert counts[(lugs_dn, CAPABILITY_METER)] == 5
    assert counts[(lugs_dn, CAPABILITY_CONNECTION)] == 4

    # BESS: info(6 — software-version pre-rename) + soc(2) = 8.
    assert counts[(bess, CAPABILITY_INFO)] == 6
    assert counts[(bess, CAPABILITY_SOC)] == 2

    # MID grandchild: info(6) + grid(3) = 9.
    assert counts[(mid, CAPABILITY_INFO)] == 6
    assert counts[(mid, CAPABILITY_GRID)] == 3

    # Circuits: 11 × 18 specs each = 198.
    circuit_devices = {
        did for did, dev in panel_a_devices.items()
        if dev["description"].get("type") == "energy.ebus.device.circuit"
    }
    assert len(circuit_devices) == 11
    for cid in circuit_devices:
        assert counts[(cid, CAPABILITY_INFO)] == 4
        assert counts[(cid, CAPABILITY_METER)] == 4
        assert counts[(cid, CAPABILITY_SWITCH)] == 2
        assert counts[(cid, CAPABILITY_PRIORITY)] == 4
        assert counts[(cid, CAPABILITY_CONNECTION)] == 4

    assert {s.device_id for s in specs} == {panel, lugs_up, lugs_dn, bess, mid} | circuit_devices
    assert len(specs) == 279  # 44 panel + 10 + 10 lugs + 8 BESS + 9 MID + 11 × 18 circuits


def test_walk_panel_a_upstream_lugs_use_grid_energy_names(panel_a_devices: dict[str, Any]) -> None:
    """Upstream lugs use the friendly 'Energy' / 'Energy Returned' names.

    This is the Energy Dashboard wiring source documented in the README.
    """
    specs = entities_from_tree(panel_a_devices)
    up_energy = [
        s for s in specs
        if s.device_id == "nt-0000-abc12-lugs-up"
        and s.capability == CAPABILITY_METER
        and s.property_id in {"imported-energy", "exported-energy"}
    ]
    by_id = {s.property_id: s for s in up_energy}
    assert by_id["imported-energy"].name == "Energy"
    assert by_id["exported-energy"].name == "Energy Returned"


def test_walk_panel_b_snapshot_includes_pv_and_more_circuits(panel_b_devices: dict[str, Any]) -> None:
    """panel_b adds PV and 19 circuits on top of panel_a's surface.

    Spec totals: 86 (non-circuit, including PV) + 19 × 18 = 86 + 342 = 428.
    """
    specs = entities_from_tree(panel_b_devices)
    counts = _by_device_capability(specs)

    panel = "nt-0000-def34"
    pv = "nt-0000-def34-pv0001"

    # PV: info(5) — vendor, product, serial, software-version (pre-rename), nameplate-capacity (W).
    assert counts[(pv, CAPABILITY_INFO)] == 5

    circuit_devices = {
        did for did, dev in panel_b_devices.items()
        if dev["description"].get("type") == "energy.ebus.device.circuit"
    }
    assert len(circuit_devices) == 19

    non_circuit = {
        panel,
        "nt-0000-def34-lugs-up",
        "nt-0000-def34-lugs-dn",
        "nt-0000-def34-bess0001",
        "nt-0000-def34-bess0001-mid",
        pv,
    }
    assert {s.device_id for s in specs} == non_circuit | circuit_devices
    assert len(specs) == 428


def test_walk_empty_tree_returns_empty_list() -> None:
    assert entities_from_tree({}) == []


def test_walk_skips_non_ebus_device_types() -> None:
    """A device with a non-eBus type is logged and skipped, doesn't raise."""
    devices = {
        "rogue-thermostat": {
            "description": {
                "type": "io.somevendor.thermostat",
                "nodes": {"setpoint": {"properties": {"target": {}}}},
            },
        },
    }
    assert entities_from_tree(devices) == []


def test_entityspec_has_tree_position_fields() -> None:
    """Guard against accidental removal of the device_id / capability fields."""
    spec = EntitySpec(
        device_id="dev",
        capability="info",
        property_id="x",
        platform=Platform.SENSOR,
        name="X",
    )
    assert spec.device_id == "dev"
    assert spec.capability == "info"
    assert spec.via_device_id == ""
