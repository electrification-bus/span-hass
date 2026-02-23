"""Tests for SPAN switch platform."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from homeassistant.const import Platform

from custom_components.span_ebus.node_mappers import EntitySpec
from custom_components.span_ebus.switch import SpanEbusSwitch

from .conftest import MOCK_CIRCUIT_UUID, MOCK_SERIAL


@pytest.fixture
def mock_panel():
    panel = MagicMock()
    panel.serial_number = MOCK_SERIAL
    panel.available = True
    panel.register_property_callback = MagicMock(return_value=lambda: None)
    panel.register_availability_callback = MagicMock(return_value=lambda: None)
    panel.get_property_value = MagicMock(return_value=None)
    panel.set_property = MagicMock(return_value=True)
    return panel


@pytest.fixture
def relay_spec():
    return EntitySpec(
        platform=Platform.SWITCH,
        node_id=MOCK_CIRCUIT_UUID,
        property_id="relay",
        name="Kitchen Relay",
        settable=True,
        icon="mdi:electric-switch",
    )


def test_switch_closed_is_on(mock_panel, relay_spec):
    """Test relay CLOSED maps to is_on=True."""
    switch = SpanEbusSwitch(mock_panel, relay_spec)
    switch._update_from_value("CLOSED")
    assert switch._attr_is_on is True


def test_switch_open_is_off(mock_panel, relay_spec):
    """Test relay OPEN maps to is_on=False."""
    switch = SpanEbusSwitch(mock_panel, relay_spec)
    switch._update_from_value("OPEN")
    assert switch._attr_is_on is False


def test_switch_case_insensitive(mock_panel, relay_spec):
    """Test relay value parsing is case-insensitive."""
    switch = SpanEbusSwitch(mock_panel, relay_spec)
    switch._update_from_value("closed")
    assert switch._attr_is_on is True


async def test_turn_on(mock_panel, relay_spec):
    """Test turn_on sends CLOSED."""
    switch = SpanEbusSwitch(mock_panel, relay_spec)
    await switch.async_turn_on()
    mock_panel.set_property.assert_called_once_with(MOCK_CIRCUIT_UUID, "relay", "CLOSED")


async def test_turn_off(mock_panel, relay_spec):
    """Test turn_off sends OPEN."""
    switch = SpanEbusSwitch(mock_panel, relay_spec)
    await switch.async_turn_off()
    mock_panel.set_property.assert_called_once_with(MOCK_CIRCUIT_UUID, "relay", "OPEN")
