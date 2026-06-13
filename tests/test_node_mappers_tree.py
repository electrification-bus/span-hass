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
    UnitOfPower,
    UnitOfTime,
)
import pytest

from custom_components.span_ebus.const import (
    CAPABILITY_DOOR,
    CAPABILITY_INFO,
    CAPABILITY_METER,
    CAPABILITY_PCS,
    CAPABILITY_POWER_FLOWS,
    CAPABILITY_SHED,
    CAPABILITY_SHED_FORECAST,
    CAPABILITY_STATUS,
    DEVICE_TYPE_DISTRIBUTION_ENCLOSURE,
)
from custom_components.span_ebus.node_mappers_tree import (
    CAPABILITY_MAPPERS,
    EntitySpec,
    _map_enclosure_door,
    _map_enclosure_info,
    _map_enclosure_meter,
    _map_enclosure_pcs,
    _map_enclosure_power_flows,
    _map_enclosure_shed,
    _map_enclosure_shed_forecast,
    _map_enclosure_status,
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


def test_map_enclosure_status_emits_seven_specs_from_panel-a_shape() -> None:
    """panel-a publishes all 7 status properties (with legacy vendor-cloud name)."""
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


def test_map_enclosure_pcs_emits_17_specs_for_panel-a() -> None:
    """panel-a publishes the full PCS surface: 4 fixed + 5 limit families × 3 = 17."""
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


# ── Dispatch table ────────────────────────────────────────────────────────


def test_dispatch_table_covers_26_capabilities() -> None:
    """Every (device-class, capability) pair from the design doc has a mapper."""
    assert len(CAPABILITY_MAPPERS) == 26


def test_dispatch_table_includes_distribution_enclosure_info_and_door() -> None:
    assert CAPABILITY_MAPPERS[(DEVICE_TYPE_DISTRIBUTION_ENCLOSURE, CAPABILITY_INFO)] is _map_enclosure_info
    assert CAPABILITY_MAPPERS[(DEVICE_TYPE_DISTRIBUTION_ENCLOSURE, CAPABILITY_DOOR)] is _map_enclosure_door


# ── entities_from_tree against real snapshots ────────────────────────────


@pytest.fixture
def panel-a_devices() -> dict[str, Any]:
    return _load("nt-0000-abc12.json")


@pytest.fixture
def panel-b_devices() -> dict[str, Any]:
    return _load("nt-0000-def34.json")


def test_walk_panel-a_snapshot_produces_enclosure_specs(panel-a_devices: dict[str, Any]) -> None:
    """Walk the panel-a snapshot end-to-end.

    The panel publishes all 8 enclosure capabilities; lugs/BESS/MID/circuits are
    still unmapped, so every spec emitted here belongs to the panel root.
    """
    specs = entities_from_tree(panel-a_devices)

    panel_id = "nt-0000-abc12"
    assert all(s.device_id == panel_id for s in specs)

    # Counts per capability against the canonical panel-a snapshot.
    by_cap: dict[str, int] = {}
    for s in specs:
        by_cap[s.capability] = by_cap.get(s.capability, 0) + 1

    assert by_cap[CAPABILITY_INFO] == 6              # vendor, model, serial, HW, FW, data-model
    assert by_cap[CAPABILITY_DOOR] == 1
    assert by_cap[CAPABILITY_METER] == 2             # l1/l2 voltage
    assert by_cap[CAPABILITY_STATUS] == 7
    assert by_cap[CAPABILITY_PCS] == 17              # 4 fixed + import-limit alone + 4 limits × 3
    assert by_cap[CAPABILITY_POWER_FLOWS] == 4
    assert by_cap[CAPABILITY_SHED_FORECAST] == 5
    assert by_cap[CAPABILITY_SHED] == 2

    assert len(specs) == sum(by_cap.values()) == 44


def test_walk_panel-b_snapshot_panel_root_only(panel-b_devices: dict[str, Any]) -> None:
    """Walk panel-b (which adds PV vs panel-a).

    All 24 lugs/BESS/MID/PV/EVSE/circuit mappers are still stubs so every spec
    belongs to the panel root. Same enclosure surface as panel-a (8 capabilities).
    """
    specs = entities_from_tree(panel-b_devices)

    panel_id = "nt-0000-def34"
    assert {s.device_id for s in specs} == {panel_id}
    # Same enclosure capability layout as panel-a → same total spec count.
    assert len(specs) == 44


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
