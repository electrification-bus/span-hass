"""Tests for SPAN sensor platform."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import Platform, UnitOfEnergy, UnitOfPower

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


# --- Energy counter decrease suppression tests ---


def _make_energy_sensor(mock_panel):
    """Create a TOTAL_INCREASING energy sensor for suppression tests."""
    spec = EntitySpec(
        platform=Platform.SENSOR,
        node_id=MOCK_CIRCUIT_UUID,
        property_id="exported-energy",
        name="Kitchen Energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit=UnitOfEnergy.WATT_HOUR,
    )
    return SpanEbusSensor(mock_panel, spec)


def test_energy_sensor_suppresses_decrease(mock_panel):
    """TOTAL_INCREASING sensor holds value when counter decreases."""
    sensor = _make_energy_sensor(mock_panel)
    sensor._update_from_value("1000.0")
    assert sensor._attr_native_value == 1000.0

    sensor._update_from_value("950.0")
    assert sensor._attr_native_value == 1000.0  # held at high-water mark


def test_energy_sensor_resumes_after_catchup(mock_panel):
    """Sensor accepts value once it exceeds the high-water mark."""
    sensor = _make_energy_sensor(mock_panel)
    sensor._update_from_value("1000.0")
    sensor._update_from_value("950.0")  # suppressed
    assert sensor._attr_native_value == 1000.0

    sensor._update_from_value("1000.0")  # catches up (equal)
    assert sensor._attr_native_value == 1000.0
    assert sensor._counter_decrease_suppressed is False

    sensor._update_from_value("1050.0")  # normal increase
    assert sensor._attr_native_value == 1050.0


def test_measurement_sensor_allows_decrease(mock_panel):
    """MEASUREMENT sensor (power) still allows decreases normally."""
    spec = EntitySpec(
        platform=Platform.SENSOR,
        node_id=MOCK_CIRCUIT_UUID,
        property_id="active-power",
        name="Kitchen Power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit=UnitOfPower.WATT,
    )
    sensor = SpanEbusSensor(mock_panel, spec)
    sensor._update_from_value("500.0")
    sensor._update_from_value("300.0")
    assert sensor._attr_native_value == 300.0


def test_energy_sensor_accepts_first_value(mock_panel):
    """First value (previous is None) is always accepted."""
    sensor = _make_energy_sensor(mock_panel)
    assert sensor._attr_native_value is None
    sensor._update_from_value("1000.0")
    assert sensor._attr_native_value == 1000.0


def test_generation_power_not_negated(mock_panel):
    """Generation power entity outputs raw value (positive for generation)."""
    spec = EntitySpec(
        platform=Platform.SENSOR,
        node_id=MOCK_CIRCUIT_UUID,
        property_id="generation-power",
        source_property_id="active-power",
        name="Generation Power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit=UnitOfPower.WATT,
        # negate defaults to False
    )
    sensor = SpanEbusSensor(mock_panel, spec)

    # PV generation: SPAN reports positive active-power for generation
    sensor._update_from_value("3500.0")
    assert sensor._attr_native_value == 3500.0

    # Nighttime standby (small negative = consumption)
    sensor._update_from_value("-5.0")
    assert sensor._attr_native_value == -5.0


def test_source_property_id_subscription(mock_panel):
    """Entity with source_property_id subscribes to source, not property_id."""
    spec = EntitySpec(
        platform=Platform.SENSOR,
        node_id=MOCK_CIRCUIT_UUID,
        property_id="generation-power",
        source_property_id="active-power",
        name="Generation Power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit=UnitOfPower.WATT,
    )
    sensor = SpanEbusSensor(mock_panel, spec)

    # unique_id uses property_id
    assert "generation-power" in sensor._attr_unique_id
    assert "active-power" not in sensor._attr_unique_id

    # source_property_id is stored for subscription
    assert sensor._source_property_id == "active-power"


def test_energy_sensor_suppression_warning_logged(mock_panel, caplog):
    """WARNING logged on first decrease, not on subsequent ones."""
    import logging

    sensor = _make_energy_sensor(mock_panel)
    sensor._update_from_value("1000.0")

    with caplog.at_level(logging.WARNING, logger="custom_components.span_ebus.sensor"):
        sensor._update_from_value("950.0")
    assert "Energy counter decrease suppressed" in caplog.text
    assert "1000.0" in caplog.text
    assert "950.0" in caplog.text

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="custom_components.span_ebus.sensor"):
        sensor._update_from_value("940.0")
    assert "Energy counter decrease suppressed" not in caplog.text  # no repeat warning
