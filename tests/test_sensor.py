"""Tests for SPAN sensor platform."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import Platform, UnitOfPower

from custom_components.span_ebus.node_mappers import EntitySpec
from custom_components.span_ebus.sensor import SpanEbusSensor

from .conftest import MOCK_CIRCUIT_UUID, MOCK_SERIAL


@pytest.fixture
def mock_panel():
    panel = MagicMock()
    panel.serial_number = MOCK_SERIAL
    panel.available = True
    panel.register_property_callback = MagicMock(return_value=lambda: None)
    panel.register_availability_callback = MagicMock(return_value=lambda: None)
    panel.get_property_value = MagicMock(return_value=None)
    return panel


def test_power_sensor_init(mock_panel):
    """Test power sensor is initialized correctly."""
    spec = EntitySpec(
        platform=Platform.SENSOR,
        node_id=MOCK_CIRCUIT_UUID,
        property_id="active-power",
        name="Kitchen Power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit=UnitOfPower.KILO_WATT,
    )
    sensor = SpanEbusSensor(mock_panel, spec)

    assert sensor._attr_unique_id == f"{MOCK_SERIAL}_{MOCK_CIRCUIT_UUID}_active-power"
    assert sensor._attr_name == "Kitchen Power"
    assert sensor._attr_device_class == SensorDeviceClass.POWER
    assert sensor._attr_state_class == SensorStateClass.MEASUREMENT
    assert sensor._attr_native_unit_of_measurement == UnitOfPower.KILO_WATT


def test_power_sensor_update_from_value(mock_panel):
    """Test power sensor parses float value."""
    spec = EntitySpec(
        platform=Platform.SENSOR,
        node_id=MOCK_CIRCUIT_UUID,
        property_id="active-power",
        name="Kitchen Power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit=UnitOfPower.KILO_WATT,
    )
    sensor = SpanEbusSensor(mock_panel, spec)
    sensor._update_from_value("150.5")
    assert sensor._attr_native_value == 150.5


def test_power_sensor_negate(mock_panel):
    """Test negate flag inverts numeric value.

    SPAN circuit active-power is negative for consumption; negate=True
    flips it so HA sees positive = consumption (device_consumption convention).
    """
    spec = EntitySpec(
        platform=Platform.SENSOR,
        node_id=MOCK_CIRCUIT_UUID,
        property_id="active-power",
        name="Kitchen Power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit=UnitOfPower.WATT,
        negate=True,
    )
    sensor = SpanEbusSensor(mock_panel, spec)
    sensor._update_from_value("-542.3")
    assert sensor._attr_native_value == 542.3

    # Positive value (e.g. PV backfeed) becomes negative
    sensor._update_from_value("100.0")
    assert sensor._attr_native_value == -100.0


def test_power_sensor_update_invalid_value(mock_panel):
    """Test power sensor handles invalid value gracefully."""
    spec = EntitySpec(
        platform=Platform.SENSOR,
        node_id=MOCK_CIRCUIT_UUID,
        property_id="active-power",
        name="Kitchen Power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit=UnitOfPower.KILO_WATT,
    )
    sensor = SpanEbusSensor(mock_panel, spec)
    sensor._update_from_value("not-a-number")
    assert sensor._attr_native_value is None


def test_string_sensor_update(mock_panel):
    """Test string sensor stores value as-is."""
    spec = EntitySpec(
        platform=Platform.SENSOR,
        node_id="core",
        property_id="software-version",
        name="Firmware Version",
    )
    sensor = SpanEbusSensor(mock_panel, spec)
    sensor._update_from_value("spanos2/r202546/03")
    assert sensor._attr_native_value == "spanos2/r202546/03"


def test_sensor_should_not_poll(mock_panel):
    """Test sensor has polling disabled."""
    spec = EntitySpec(
        platform=Platform.SENSOR,
        node_id=MOCK_CIRCUIT_UUID,
        property_id="active-power",
        name="Test",
    )
    sensor = SpanEbusSensor(mock_panel, spec)
    assert sensor.should_poll is False


def test_subdevice_sensor_device_info(mock_panel):
    """Test circuit sensor gets sub-device DeviceInfo with via_device."""
    from custom_components.span_ebus.const import DOMAIN

    spec = EntitySpec(
        platform=Platform.SENSOR,
        node_id=MOCK_CIRCUIT_UUID,
        property_id="active-power",
        name="Power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit=UnitOfPower.KILO_WATT,
        node_type="energy.ebus.device.circuit",
        device_name="Kitchen",
    )
    sensor = SpanEbusSensor(mock_panel, spec)

    info = sensor._attr_device_info
    assert info["identifiers"] == {(DOMAIN, f"{MOCK_SERIAL}_{MOCK_CIRCUIT_UUID}")}
    # Entity device_info includes the device name for consistent entity_id generation.
    # Full device registration (model, via_device) is done by _register_subdevices.
    assert info["name"] == "Kitchen"


def test_feed_resolves_circuit_name(mock_panel):
    """Test feed sensor resolves circuit UUID to user-assigned name."""
    mock_panel.get_property_value = MagicMock(
        side_effect=lambda node_id, prop_id: "Kitchen" if prop_id == "name" else None
    )
    spec = EntitySpec(
        platform=Platform.SENSOR,
        node_id="bess-uuid",
        property_id="feed",
        name="Feed Circuit",
    )
    sensor = SpanEbusSensor(mock_panel, spec)
    sensor._update_from_value("circuit-uuid-abc123")

    assert sensor._attr_native_value == "Kitchen"
    assert sensor._attr_extra_state_attributes == {"circuit_id": "circuit-uuid-abc123"}


def test_feed_falls_back_to_uuid(mock_panel):
    """Test feed sensor shows raw UUID when circuit name is unavailable."""
    spec = EntitySpec(
        platform=Platform.SENSOR,
        node_id="bess-uuid",
        property_id="feed",
        name="Feed Circuit",
    )
    sensor = SpanEbusSensor(mock_panel, spec)
    sensor._update_from_value("circuit-uuid-abc123")

    assert sensor._attr_native_value == "circuit-uuid-abc123"


def test_panel_sensor_device_info(mock_panel):
    """Test core sensor gets panel DeviceInfo (no via_device)."""
    from custom_components.span_ebus.const import DOMAIN

    spec = EntitySpec(
        platform=Platform.SENSOR,
        node_id="core",
        property_id="software-version",
        name="Firmware Version",
    )
    sensor = SpanEbusSensor(mock_panel, spec)

    info = sensor._attr_device_info
    assert info["identifiers"] == {(DOMAIN, MOCK_SERIAL)}
    assert "via_device" not in info
