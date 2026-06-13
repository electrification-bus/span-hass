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
from homeassistant.const import EntityCategory, Platform
import pytest

from custom_components.span_ebus.const import (
    CAPABILITY_DOOR,
    CAPABILITY_INFO,
    DEVICE_TYPE_DISTRIBUTION_ENCLOSURE,
)
from custom_components.span_ebus.node_mappers_tree import (
    CAPABILITY_MAPPERS,
    EntitySpec,
    _map_enclosure_door,
    _map_enclosure_info,
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
    """The panel-a panel has info (6 props) + door (1 prop). Phase 1 should emit 7 specs total."""
    specs = entities_from_tree(panel-a_devices)

    info_specs = [s for s in specs if s.capability == CAPABILITY_INFO]
    door_specs = [s for s in specs if s.capability == CAPABILITY_DOOR]

    # Every spec should belong to the panel root, because no other mapper is implemented yet.
    panel_id = "nt-0000-abc12"
    assert all(s.device_id == panel_id for s in specs)

    # panel-a info declares 6 properties: vendor-name, model, serial-number,
    # hardware-version, software-version (pre-rename), data-model-version.
    assert len(info_specs) == 6

    # Door has one property. The snapshot predates the firmware-side rename to
    # door/state and still publishes door/door — the mapper picks it up either way.
    assert len(door_specs) == 1
    assert door_specs[0].property_id in {"state", "door"}


def test_walk_panel-b_snapshot_does_not_crash(panel-b_devices: dict[str, Any]) -> None:
    """Walk panel-b (which adds PV vs panel-a).

    All 24 unimplemented mappers should return [] without crashing.
    """
    specs = entities_from_tree(panel-b_devices)

    # Only the panel root's info + door specs land in Phase 1.
    panel_id = "nt-0000-def34"
    assert {s.device_id for s in specs} == {panel_id}


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
