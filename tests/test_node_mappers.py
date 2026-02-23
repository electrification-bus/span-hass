"""Tests for node_mappers — $description → HA entity specs."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import (
    EntityCategory,
    Platform,
    UnitOfElectricCurrent,
    UnitOfEnergy,
    UnitOfPower,
)

from custom_components.span_ebus.node_mappers import (
    _parse_enum_format,
    entities_from_description,
)

from .conftest import MOCK_CIRCUIT_UUID, MOCK_DESCRIPTION


def test_entities_from_description():
    """Test that entities are created from mock description."""
    specs = entities_from_description(MOCK_DESCRIPTION)
    assert len(specs) > 0

    # Check we got the expected platforms
    platforms = {s.platform for s in specs}
    assert Platform.SENSOR in platforms
    assert Platform.BINARY_SENSOR in platforms
    assert Platform.SWITCH in platforms
    assert Platform.SELECT in platforms


def test_core_door_binary_sensor():
    """Test core door produces a tamper binary sensor."""
    specs = entities_from_description(MOCK_DESCRIPTION)
    door = [s for s in specs if s.node_id == "core" and s.property_id == "door"]
    assert len(door) == 1
    assert door[0].platform == Platform.BINARY_SENSOR
    assert door[0].device_class == BinarySensorDeviceClass.TAMPER


def test_core_ethernet_connectivity():
    """Test core ethernet produces a connectivity binary sensor."""
    specs = entities_from_description(MOCK_DESCRIPTION)
    eth = [s for s in specs if s.node_id == "core" and s.property_id == "ethernet"]
    assert len(eth) == 1
    assert eth[0].device_class == BinarySensorDeviceClass.CONNECTIVITY
    assert eth[0].entity_category == EntityCategory.DIAGNOSTIC


def test_core_software_version_diagnostic_sensor():
    """Test core software-version produces a diagnostic sensor."""
    specs = entities_from_description(MOCK_DESCRIPTION)
    fw = [s for s in specs if s.property_id == "software-version"]
    assert len(fw) == 1
    assert fw[0].platform == Platform.SENSOR
    assert fw[0].entity_category == EntityCategory.DIAGNOSTIC


def test_circuit_relay_switch():
    """Test circuit relay produces a switch."""
    specs = entities_from_description(MOCK_DESCRIPTION)
    relay = [
        s for s in specs
        if s.node_id == MOCK_CIRCUIT_UUID and s.property_id == "relay"
    ]
    assert len(relay) == 1
    assert relay[0].platform == Platform.SWITCH
    assert relay[0].settable is True


def test_circuit_active_power_sensor():
    """Test circuit active-power produces a power sensor in W.

    Firmware bug: schema declares kW but values are actually watts.
    """
    specs = entities_from_description(MOCK_DESCRIPTION)
    power = [
        s for s in specs
        if s.node_id == MOCK_CIRCUIT_UUID and s.property_id == "active-power"
    ]
    assert len(power) == 1
    assert power[0].device_class == SensorDeviceClass.POWER
    assert power[0].state_class == SensorStateClass.MEASUREMENT
    assert power[0].native_unit == UnitOfPower.WATT
    assert power[0].negate is True


def test_circuit_energy_sensors():
    """Test circuit energy properties produce TOTAL_INCREASING sensors."""
    specs = entities_from_description(MOCK_DESCRIPTION)
    energy = [
        s for s in specs
        if s.node_id == MOCK_CIRCUIT_UUID and "energy" in s.property_id
    ]
    assert len(energy) == 2
    for e in energy:
        assert e.device_class == SensorDeviceClass.ENERGY
        assert e.state_class == SensorStateClass.TOTAL_INCREASING
        assert e.native_unit == UnitOfEnergy.WATT_HOUR


def test_circuit_shed_priority_select():
    """Test circuit shed-priority produces a select with options."""
    specs = entities_from_description(MOCK_DESCRIPTION)
    priority = [
        s for s in specs
        if s.node_id == MOCK_CIRCUIT_UUID and s.property_id == "shed-priority"
    ]
    assert len(priority) == 1
    assert priority[0].platform == Platform.SELECT
    assert priority[0].options == ["MUST_HAVE", "NICE_TO_HAVE", "NON_ESSENTIAL"]
    assert priority[0].settable is True


def test_circuit_naming_with_panel():
    """Test circuit device_name uses panel name when available."""
    from unittest.mock import MagicMock

    panel = MagicMock()
    panel.serial_number = "nt-0000-abc12"
    panel.get_property_value = MagicMock(return_value="Kitchen")

    specs = entities_from_description(MOCK_DESCRIPTION, panel=panel)
    relay = [
        s for s in specs
        if s.node_id == MOCK_CIRCUIT_UUID and s.property_id == "relay"
    ]
    assert len(relay) == 1
    # Entity name is relative (just "Relay"), device_name has the circuit label
    assert relay[0].name == "Relay"
    assert relay[0].device_name == "Kitchen"
    assert relay[0].node_type == "energy.ebus.device.circuit"


def test_circuit_naming_fallback():
    """Test circuit device_name falls back to short UUID without panel."""
    specs = entities_from_description(MOCK_DESCRIPTION)
    relay = [
        s for s in specs
        if s.node_id == MOCK_CIRCUIT_UUID and s.property_id == "relay"
    ]
    assert len(relay) == 1
    # Entity name is relative, device_name uses UUID fallback
    assert relay[0].name == "Relay"
    assert MOCK_CIRCUIT_UUID[:6] in relay[0].device_name


def test_power_flows_sensors():
    """Test power-flows node produces power sensors."""
    specs = entities_from_description(MOCK_DESCRIPTION)
    pf = [s for s in specs if s.node_id == "power-flows"]
    assert len(pf) == 2
    for s in pf:
        assert s.device_class == SensorDeviceClass.POWER
        assert s.native_unit == UnitOfPower.WATT


def test_parse_enum_format():
    """Test parsing Homie enum format strings."""
    assert _parse_enum_format("OPEN,CLOSED") == ["OPEN", "CLOSED"]
    assert _parse_enum_format("a, b, c") == ["a", "b", "c"]
    assert _parse_enum_format("") == []


def test_unknown_nodes_skipped():
    """Test that unknown node types are silently skipped."""
    desc = {
        "nodes": {
            "unknown-thing": {
                "name": "Unknown",
                "type": "mystery",
                "properties": {
                    "something": {"name": "Foo", "datatype": "string"},
                },
            }
        }
    }
    specs = entities_from_description(desc)
    assert len(specs) == 0


def test_empty_description():
    """Test that empty description produces no specs."""
    assert entities_from_description({}) == []
    assert entities_from_description({"nodes": {}}) == []


def test_bess_node():
    """Test battery storage node creates SOC, SOE, and power sensors."""
    desc = {
        "nodes": {
            "bess": {
                "name": "Battery",
                "type": "energy.ebus.device.bess",
                "properties": {
                    "soc": {
                        "name": "State of Charge",
                        "datatype": "float",
                        "unit": "%",
                    },
                    "soe": {
                        "name": "State of Energy",
                        "datatype": "float",
                        "unit": "kWh",
                    },
                    "power-w": {
                        "name": "Battery Power",
                        "datatype": "float",
                        "unit": "W",
                    },
                },
            }
        }
    }
    specs = entities_from_description(desc)
    assert len(specs) == 3
    soc = [s for s in specs if s.property_id == "soc"][0]
    assert soc.device_class == SensorDeviceClass.BATTERY
    assert soc.native_unit == "%"
    soe = [s for s in specs if s.property_id == "soe"][0]
    assert soe.device_class == SensorDeviceClass.ENERGY_STORAGE
    assert soe.native_unit == UnitOfEnergy.KILO_WATT_HOUR
    power = [s for s in specs if "power" in s.property_id][0]
    assert power.device_class == SensorDeviceClass.POWER


def test_pv_node():
    """Test PV (solar) node creates power/energy sensors."""
    desc = {
        "nodes": {
            "pv": {
                "name": "Solar",
                "type": "energy.ebus.device.pv",
                "properties": {
                    "power-w": {"name": "Power", "datatype": "float", "unit": "W"},
                    "energy-wh": {"name": "Energy", "datatype": "float", "unit": "Wh"},
                },
            }
        }
    }
    specs = entities_from_description(desc)
    assert len(specs) == 2


def test_circuit_current_sensor():
    """Test circuit current property produces a current sensor."""
    desc = {
        "nodes": {
            "uuid-node": {
                "name": "Test Circuit",
                "type": "energy.ebus.device.circuit",
                "properties": {
                    "current": {
                        "name": "Current",
                        "datatype": "float",
                        "unit": "A",
                    },
                },
            }
        }
    }
    specs = entities_from_description(desc)
    assert len(specs) == 1
    assert specs[0].device_class == SensorDeviceClass.CURRENT
    assert specs[0].native_unit == UnitOfElectricCurrent.AMPERE


def test_circuit_breaker_rating_sensor():
    """Test circuit breaker-rating produces a diagnostic sensor."""
    desc = {
        "nodes": {
            "uuid-node": {
                "name": "Test Circuit",
                "type": "energy.ebus.device.circuit",
                "properties": {
                    "breaker-rating": {
                        "name": "Breaker Rating",
                        "datatype": "integer",
                        "unit": "A",
                    },
                },
            }
        }
    }
    specs = entities_from_description(desc)
    assert len(specs) == 1
    assert specs[0].device_class == SensorDeviceClass.CURRENT
    assert specs[0].native_unit == UnitOfElectricCurrent.AMPERE
    assert specs[0].entity_category == EntityCategory.DIAGNOSTIC


def test_core_voltage_sensors():
    """Test core L1/L2 voltage properties produce voltage sensors."""
    desc = {
        "nodes": {
            "core": {
                "name": "Core",
                "type": "energy.ebus.device.distribution-enclosure.core",
                "properties": {
                    "l1-voltage": {
                        "name": "L1 Voltage",
                        "datatype": "float",
                        "unit": "V",
                    },
                    "l2-voltage": {
                        "name": "L2 Voltage",
                        "datatype": "float",
                        "unit": "V",
                    },
                },
            }
        }
    }
    specs = entities_from_description(desc)
    assert len(specs) == 2
    for s in specs:
        assert s.device_class == SensorDeviceClass.VOLTAGE
        assert s.state_class == SensorStateClass.MEASUREMENT


def test_humanize_abbreviations():
    """Test _humanize handles known abbreviations correctly."""
    from custom_components.span_ebus.node_mappers import _humanize

    assert _humanize("pv-power") == "PV Power"
    assert _humanize("ev-charger") == "EV Charger"
    assert _humanize("soc") == "SOC"
    assert _humanize("soe") == "SOE"
    assert _humanize("grid-power") == "Grid Power"


def test_core_entities_have_no_subdevice():
    """Test core entities have empty node_type and device_name."""
    specs = entities_from_description(MOCK_DESCRIPTION)
    core_specs = [s for s in specs if s.node_id == "core"]
    assert len(core_specs) > 0
    for s in core_specs:
        assert s.node_type == ""
        assert s.device_name == ""


def test_circuit_specs_have_subdevice_fields():
    """Test circuit entity specs have node_type and device_name set."""
    specs = entities_from_description(MOCK_DESCRIPTION)
    circuit_specs = [s for s in specs if s.node_id == MOCK_CIRCUIT_UUID]
    assert len(circuit_specs) > 0
    for s in circuit_specs:
        assert s.node_type == "energy.ebus.device.circuit"
        assert s.device_name != ""


def test_bess_specs_have_subdevice_fields():
    """Test BESS entity specs have node_type and friendly device_name."""
    desc = {
        "nodes": {
            "bess": {
                "name": "Distribution Enclosure Commissioned Backup System",
                "type": "energy.ebus.device.bess",
                "properties": {
                    "soc": {"name": "SOC", "datatype": "float", "unit": "%"},
                },
            }
        }
    }
    specs = entities_from_description(desc)
    assert len(specs) == 1
    assert specs[0].node_type == "energy.ebus.device.bess"
    assert specs[0].device_name == "Battery Storage"
    assert specs[0].name == "State of Charge"


def test_pv_specs_have_subdevice_fields():
    """Test PV entity specs have node_type and friendly device_name."""
    desc = {
        "nodes": {
            "pv": {
                "name": "Distribution Enclosure Commissioned PV System",
                "type": "energy.ebus.device.pv",
                "properties": {
                    "power-w": {"name": "Power", "datatype": "float", "unit": "W"},
                },
            }
        }
    }
    specs = entities_from_description(desc)
    assert len(specs) == 1
    assert specs[0].node_type == "energy.ebus.device.pv"
    assert specs[0].device_name == "Solar PV"
    assert specs[0].name == "Power W"


def test_power_flow_entities_are_subdevice():
    """Test power-flow entities create a Site Metering sub-device."""
    specs = entities_from_description(MOCK_DESCRIPTION)
    pf_specs = [s for s in specs if s.node_id == "power-flows"]
    assert len(pf_specs) > 0
    for s in pf_specs:
        assert s.node_type == "energy.ebus.device.power-flows"
        assert s.device_name == "Site Metering"


def test_bess_device_name_with_serial_suffix():
    """Test BESS device_name includes short serial suffix when panel is provided."""
    from unittest.mock import MagicMock

    panel = MagicMock()
    panel.serial_number = "nt-2024-g5h6j"
    panel.get_property_value = MagicMock(return_value=None)

    desc = {
        "nodes": {
            "bess": {
                "name": "Distribution Enclosure Commissioned Backup System",
                "type": "energy.ebus.device.bess",
                "properties": {
                    "soc": {"name": "SOC", "datatype": "float", "unit": "%"},
                },
            }
        }
    }
    specs = entities_from_description(desc, panel=panel)
    assert len(specs) == 1
    assert specs[0].device_name == "g5h6j Battery Storage"


def test_pv_device_name_with_serial_suffix():
    """Test PV device_name includes short serial suffix when panel is provided."""
    from unittest.mock import MagicMock

    panel = MagicMock()
    panel.serial_number = "nt-2024-a1b2c"
    panel.get_property_value = MagicMock(return_value=None)

    desc = {
        "nodes": {
            "pv": {
                "name": "Distribution Enclosure Commissioned PV System",
                "type": "energy.ebus.device.pv",
                "properties": {
                    "power-w": {"name": "Power", "datatype": "float", "unit": "W"},
                },
            }
        }
    }
    specs = entities_from_description(desc, panel=panel)
    assert len(specs) == 1
    assert specs[0].device_name == "a1b2c Solar PV"


def test_power_flows_device_name_with_serial_suffix():
    """Test power-flows device_name includes short serial suffix when panel is provided."""
    from unittest.mock import MagicMock

    panel = MagicMock()
    panel.serial_number = "nt-2024-g5h6j"
    panel.get_property_value = MagicMock(return_value=None)

    specs = entities_from_description(MOCK_DESCRIPTION, panel=panel)
    pf_specs = [s for s in specs if s.node_id == "power-flows"]
    assert len(pf_specs) > 0
    for s in pf_specs:
        assert s.device_name == "g5h6j Site Metering"


def test_subdevice_info_via_device():
    """Test subdevice_info creates DeviceInfo with correct via_device."""
    from custom_components.span_ebus.const import DOMAIN
    from custom_components.span_ebus.util import subdevice_info

    info = subdevice_info(
        serial="nt-0000-abc12",
        node_id="uuid-123",
        node_type="energy.ebus.device.circuit",
        name="Kitchen",
    )
    assert info["identifiers"] == {(DOMAIN, "nt-0000-abc12_uuid-123")}
    assert info["name"] == "Kitchen"
    assert info["manufacturer"] == "SPAN"
    assert info["model"] == "Circuit"
    assert info["via_device"] == (DOMAIN, "nt-0000-abc12")


def test_subdevice_info_bess_model():
    """Test subdevice_info uses correct model label for BESS."""
    from custom_components.span_ebus.util import subdevice_info

    info = subdevice_info(
        serial="nt-0000-abc12",
        node_id="bess-1",
        node_type="energy.ebus.device.bess",
        name="Powerwall",
    )
    assert info["model"] == "Battery Storage"


def test_subdevice_info_pv_model():
    """Test subdevice_info uses correct model label for PV."""
    from custom_components.span_ebus.util import subdevice_info

    info = subdevice_info(
        serial="nt-0000-abc12",
        node_id="pv-1",
        node_type="energy.ebus.device.pv",
        name="Solar",
    )
    assert info["model"] == "Solar PV"


def test_subdevice_info_evse_model():
    """Test subdevice_info uses correct model label for EVSE."""
    from custom_components.span_ebus.util import subdevice_info

    info = subdevice_info(
        serial="nt-0000-abc12",
        node_id="evse-1",
        node_type="energy.ebus.device.evse",
        name="Charger",
    )
    assert info["model"] == "EV Charger"


def test_dominant_power_source_is_select():
    """Test dominant-power-source produces a settable Select entity."""
    specs = entities_from_description(MOCK_DESCRIPTION)
    dps = [s for s in specs if s.property_id == "dominant-power-source"]
    assert len(dps) == 1
    assert dps[0].platform == Platform.SELECT
    assert dps[0].settable is True
    assert "GRID" in dps[0].options
    assert "BATTERY" in dps[0].options
    assert "PV" in dps[0].options


def test_core_door_has_on_values():
    """Test door binary sensor has on_values set for enum handling."""
    specs = entities_from_description(MOCK_DESCRIPTION)
    door = [s for s in specs if s.property_id == "door"]
    assert len(door) == 1
    assert door[0].on_values == {"OPEN"}


def test_core_relay_has_on_values():
    """Test main relay binary sensor has on_values for CLOSED=on."""
    specs = entities_from_description(MOCK_DESCRIPTION)
    relay = [s for s in specs if s.node_id == "core" and s.property_id == "relay"]
    assert len(relay) == 1
    assert relay[0].on_values == {"CLOSED"}


def test_circuit_space_diagnostic():
    """Test circuit space property produces a diagnostic sensor."""
    specs = entities_from_description(MOCK_DESCRIPTION)
    space = [
        s for s in specs
        if s.node_id == MOCK_CIRCUIT_UUID and s.property_id == "space"
    ]
    assert len(space) == 1
    assert space[0].platform == Platform.SENSOR
    assert space[0].entity_category == EntityCategory.DIAGNOSTIC


def test_circuit_dipole_binary_sensor():
    """Test circuit dipole property produces a diagnostic binary sensor."""
    specs = entities_from_description(MOCK_DESCRIPTION)
    dipole = [
        s for s in specs
        if s.node_id == MOCK_CIRCUIT_UUID and s.property_id == "dipole"
    ]
    assert len(dipole) == 1
    assert dipole[0].platform == Platform.BINARY_SENSOR
    assert dipole[0].entity_category == EntityCategory.DIAGNOSTIC


def test_circuit_pcs_priority_sensor():
    """Test circuit pcs-priority produces a sensor (integer, not select)."""
    specs = entities_from_description(MOCK_DESCRIPTION)
    pcs = [
        s for s in specs
        if s.node_id == MOCK_CIRCUIT_UUID and s.property_id == "pcs-priority"
    ]
    assert len(pcs) == 1
    assert pcs[0].platform == Platform.SENSOR


def test_bess_metadata_properties():
    """Test BESS node maps all metadata properties."""
    desc = {
        "nodes": {
            "bess": {
                "name": "Battery",
                "type": "energy.ebus.device.bess",
                "properties": {
                    "soc": {"name": "SOC", "datatype": "float", "unit": "%"},
                    "vendor-name": {"name": "Vendor", "datatype": "string"},
                    "serial-number": {"name": "Serial", "datatype": "string"},
                    "nameplate-capacity": {
                        "name": "Capacity",
                        "datatype": "float",
                        "unit": "kWh",
                    },
                    "feed": {"name": "Feed", "datatype": "enum", "format": "L1,L2"},
                },
            }
        }
    }
    specs = entities_from_description(desc)
    assert len(specs) == 5
    vendor = [s for s in specs if s.property_id == "vendor-name"][0]
    assert vendor.entity_category == EntityCategory.DIAGNOSTIC
    cap = [s for s in specs if s.property_id == "nameplate-capacity"][0]
    assert cap.device_class == SensorDeviceClass.ENERGY_STORAGE
    assert cap.native_unit == UnitOfEnergy.KILO_WATT_HOUR


def test_bess_nameplate_capacity_wh_unit():
    """Test BESS nameplate-capacity respects Wh unit from $description."""
    desc = {
        "nodes": {
            "bess": {
                "name": "Battery",
                "type": "energy.ebus.device.bess",
                "properties": {
                    "nameplate-capacity": {
                        "name": "Capacity",
                        "datatype": "float",
                        "unit": "Wh",
                    },
                },
            }
        }
    }
    specs = entities_from_description(desc)
    cap = specs[0]
    assert cap.native_unit == UnitOfEnergy.WATT_HOUR


def test_pv_metadata_properties():
    """Test PV node maps metadata properties."""
    desc = {
        "nodes": {
            "pv": {
                "name": "Solar",
                "type": "energy.ebus.device.pv",
                "properties": {
                    "vendor-name": {"name": "Vendor", "datatype": "string"},
                    "nameplate-capacity": {
                        "name": "Capacity",
                        "datatype": "float",
                        "unit": "kW",
                    },
                    "feed": {"name": "Feed", "datatype": "enum", "format": "L1,L2"},
                },
            }
        }
    }
    specs = entities_from_description(desc)
    assert len(specs) == 3
    cap = [s for s in specs if s.property_id == "nameplate-capacity"][0]
    assert cap.device_class == SensorDeviceClass.POWER
    assert cap.native_unit == UnitOfPower.KILO_WATT


def test_pv_nameplate_capacity_watts_unit():
    """Test PV nameplate-capacity respects W unit from $description."""
    desc = {
        "nodes": {
            "pv": {
                "name": "Solar",
                "type": "energy.ebus.device.pv",
                "properties": {
                    "nameplate-capacity": {
                        "name": "Capacity",
                        "datatype": "float",
                        "unit": "W",
                    },
                },
            }
        }
    }
    specs = entities_from_description(desc)
    cap = specs[0]
    assert cap.native_unit == UnitOfPower.WATT


def test_evse_comprehensive():
    """Test EVSE node maps status, lock-state, advertised-current, metadata."""
    desc = {
        "nodes": {
            "evse": {
                "name": "Charger",
                "type": "energy.ebus.device.evse",
                "properties": {
                    "status": {"name": "Status", "datatype": "enum"},
                    "lock-state": {"name": "Lock State", "datatype": "enum"},
                    "advertised-current": {
                        "name": "Current",
                        "datatype": "float",
                        "unit": "A",
                    },
                    "vendor-name": {"name": "Vendor", "datatype": "string"},
                    "software-version": {"name": "FW", "datatype": "string"},
                },
            }
        }
    }
    specs = entities_from_description(desc)
    assert len(specs) == 5
    status = [s for s in specs if s.property_id == "status"][0]
    assert status.icon == "mdi:ev-station"
    lock = [s for s in specs if s.property_id == "lock-state"][0]
    assert lock.icon == "mdi:lock"
    current = [s for s in specs if s.property_id == "advertised-current"][0]
    assert current.device_class == SensorDeviceClass.CURRENT
    assert current.native_unit == UnitOfElectricCurrent.AMPERE


def test_pcs_node():
    """Test PCS node maps boolean, current, and enum properties."""
    desc = {
        "nodes": {
            "pcs": {
                "name": "Power Control",
                "type": "energy.ebus.device.pcs",
                "properties": {
                    "enabled": {"name": "Enabled", "datatype": "boolean"},
                    "active": {"name": "Active", "datatype": "boolean"},
                    "import-limit": {
                        "name": "Import Limit",
                        "datatype": "float",
                        "unit": "A",
                    },
                    "grid-import-limit-enablement": {
                        "name": "Grid Import Limit Enablement",
                        "datatype": "enum",
                        "format": "ENABLED,DISABLED",
                    },
                },
            }
        }
    }
    specs = entities_from_description(desc)
    assert len(specs) == 4
    booleans = [s for s in specs if s.platform == Platform.BINARY_SENSOR]
    assert len(booleans) == 2
    current = [s for s in specs if s.property_id == "import-limit"][0]
    assert current.device_class == SensorDeviceClass.CURRENT
    assert current.native_unit == UnitOfElectricCurrent.AMPERE
    enum_sensor = [s for s in specs if s.property_id == "grid-import-limit-enablement"][0]
    assert enum_sensor.platform == Platform.SENSOR
    assert enum_sensor.entity_category == EntityCategory.DIAGNOSTIC


def test_lug_feed_diagnostic():
    """Test lug feed property produces a diagnostic sensor."""
    desc = {
        "nodes": {
            "upstream-lug": {
                "name": "Upstream",
                "type": "energy.ebus.device.lugs.upstream",
                "properties": {
                    "feed": {"name": "Feed", "datatype": "string"},
                    "active-power": {
                        "name": "Power",
                        "datatype": "float",
                        "unit": "W",
                    },
                },
            }
        }
    }
    specs = entities_from_description(desc)
    assert len(specs) == 2
    feed = [s for s in specs if s.property_id == "feed"][0]
    assert feed.entity_category == EntityCategory.DIAGNOSTIC
    assert "Upstream" in feed.name


def test_circuit_energy_naming():
    """Test circuit exported-energy is 'Energy' (consumption), imported is 'Energy Returned'."""
    specs = entities_from_description(MOCK_DESCRIPTION)
    imported = [
        s for s in specs
        if s.node_id == MOCK_CIRCUIT_UUID and s.property_id == "imported-energy"
    ]
    exported = [
        s for s in specs
        if s.node_id == MOCK_CIRCUIT_UUID and s.property_id == "exported-energy"
    ]
    assert len(imported) == 1
    assert imported[0].name == "Energy Returned"
    assert len(exported) == 1
    assert exported[0].name == "Energy"


def test_upstream_lug_naming():
    """Test upstream lug entities have clean names (no 'Upstream' prefix on main sensors)."""
    desc = {
        "nodes": {
            "upstream-lug": {
                "name": "Upstream",
                "type": "energy.ebus.device.lugs.upstream",
                "properties": {
                    "imported-energy": {
                        "name": "Imported Energy",
                        "datatype": "float",
                        "unit": "Wh",
                    },
                    "exported-energy": {
                        "name": "Exported Energy",
                        "datatype": "float",
                        "unit": "Wh",
                    },
                    "active-power": {
                        "name": "Power",
                        "datatype": "float",
                        "unit": "W",
                    },
                    "current": {
                        "name": "Current",
                        "datatype": "float",
                        "unit": "A",
                    },
                },
            }
        }
    }
    specs = entities_from_description(desc)
    imported = [s for s in specs if s.property_id == "imported-energy"][0]
    exported = [s for s in specs if s.property_id == "exported-energy"][0]
    power = [s for s in specs if s.property_id == "active-power"][0]
    current = [s for s in specs if s.property_id == "current"][0]
    # Upstream: clean names since device is already "Site Metering" or panel-level
    assert imported.name == "Energy"
    assert exported.name == "Energy Returned"
    assert power.name == "Power"
    # Current still gets direction prefix
    assert current.name == "Upstream Current"


def test_downstream_lug_naming():
    """Test downstream lug entities have 'Downstream' prefix."""
    desc = {
        "nodes": {
            "downstream-lug": {
                "name": "Downstream",
                "type": "energy.ebus.device.lugs.downstream",
                "properties": {
                    "imported-energy": {
                        "name": "Imported Energy",
                        "datatype": "float",
                        "unit": "Wh",
                    },
                    "exported-energy": {
                        "name": "Exported Energy",
                        "datatype": "float",
                        "unit": "Wh",
                    },
                    "active-power": {
                        "name": "Power",
                        "datatype": "float",
                        "unit": "W",
                    },
                },
            }
        }
    }
    specs = entities_from_description(desc)
    imported = [s for s in specs if s.property_id == "imported-energy"][0]
    exported = [s for s in specs if s.property_id == "exported-energy"][0]
    power = [s for s in specs if s.property_id == "active-power"][0]
    assert imported.name == "Downstream Energy"
    assert exported.name == "Downstream Energy Returned"
    assert power.name == "Downstream Power"
