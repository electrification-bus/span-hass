"""Tests for SPAN binary sensor platform."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.const import Platform

from custom_components.span_ebus.binary_sensor import SpanEbusBinarySensor
from custom_components.span_ebus.node_mappers import EntitySpec

from .conftest import MOCK_SERIAL


@pytest.fixture
def mock_panel():
    panel = MagicMock()
    panel.serial_number = MOCK_SERIAL
    panel.available = True
    panel.register_property_callback = MagicMock(return_value=lambda: None)
    panel.register_availability_callback = MagicMock(return_value=lambda: None)
    panel.get_property_value = MagicMock(return_value=None)
    return panel


def test_door_sensor_open_is_tamper(mock_panel):
    """Test door OPEN means tamper detected (is_on=True)."""
    spec = EntitySpec(
        platform=Platform.BINARY_SENSOR,
        node_id="core",
        property_id="door",
        name="Door",
        device_class=BinarySensorDeviceClass.TAMPER,
        on_values={"OPEN"},
    )
    sensor = SpanEbusBinarySensor(mock_panel, spec)

    sensor._update_from_value("OPEN")
    assert sensor._attr_is_on is True
    sensor._update_from_value("open")
    assert sensor._attr_is_on is True


def test_door_sensor_closed_is_no_tamper(mock_panel):
    """Test door CLOSED means no tamper (is_on=False)."""
    spec = EntitySpec(
        platform=Platform.BINARY_SENSOR,
        node_id="core",
        property_id="door",
        name="Door",
        device_class=BinarySensorDeviceClass.TAMPER,
        on_values={"OPEN"},
    )
    sensor = SpanEbusBinarySensor(mock_panel, spec)

    sensor._update_from_value("CLOSED")
    assert sensor._attr_is_on is False
    sensor._update_from_value("UNKNOWN")
    assert sensor._attr_is_on is False


def test_boolean_sensor_truthy_values(mock_panel):
    """Test boolean-typed binary sensors use default truthy set."""
    spec = EntitySpec(
        platform=Platform.BINARY_SENSOR,
        node_id="core",
        property_id="ethernet",
        name="Ethernet",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
    )
    sensor = SpanEbusBinarySensor(mock_panel, spec)

    for value in ("true", "1", "on", "yes"):
        sensor._update_from_value(value)
        assert sensor._attr_is_on is True, f"Expected True for '{value}'"

    for value in ("false", "0", "off"):
        sensor._update_from_value(value)
        assert sensor._attr_is_on is False, f"Expected False for '{value}'"


def test_connectivity_sensor(mock_panel):
    """Test connectivity binary sensor init."""
    spec = EntitySpec(
        platform=Platform.BINARY_SENSOR,
        node_id="core",
        property_id="ethernet",
        name="Ethernet",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
    )
    sensor = SpanEbusBinarySensor(mock_panel, spec)
    assert sensor._attr_device_class == BinarySensorDeviceClass.CONNECTIVITY

    sensor._update_from_value("connected")
    assert sensor._attr_is_on is True
