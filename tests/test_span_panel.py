"""Tests for SpanPanel wrapper."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from homeassistant.core import HomeAssistant

from custom_components.span_ebus.span_panel import SpanPanel

from .conftest import MOCK_SERIAL


@pytest.fixture
def mqtt_cfg():
    return {
        "host": "span-test.local",
        "port": 8883,
        "use_tls": True,
        "tls_ca_data": "test-cert",
        "tls_insecure": False,
        "authentication": {
            "type": "USER_PASS",
            "username": "test-user",
            "password": "test-pass",
        },
    }


@pytest.fixture
def panel(hass: HomeAssistant, mqtt_cfg):
    return SpanPanel(hass, MOCK_SERIAL, mqtt_cfg)


async def test_register_property_callback(panel: SpanPanel) -> None:
    """Test registering and unregistering property callbacks."""
    received = []

    def on_update(value: str):
        received.append(value)

    unregister = panel.register_property_callback("uuid-node", "active-power", on_update)

    # Simulate a dispatch (as if from _on_property_changed)
    panel._dispatch_property_update(("uuid-node", "active-power"), "150.5")
    assert received == ["150.5"]

    # Unregister
    unregister()
    panel._dispatch_property_update(("uuid-node", "active-power"), "200.0")
    assert received == ["150.5"]  # No new value


async def test_register_availability_callback(panel: SpanPanel) -> None:
    """Test availability callbacks."""
    states = []

    def on_availability(available: bool):
        states.append(available)

    unregister = panel.register_availability_callback(on_availability)

    panel._dispatch_availability(True)
    assert states == [True]

    panel._dispatch_availability(False)
    assert states == [True, False]

    unregister()
    panel._dispatch_availability(True)
    assert states == [True, False]  # No new state


async def test_get_property_value_no_device(panel: SpanPanel) -> None:
    """Test get_property_value when no device discovered yet."""
    assert panel.get_property_value("uuid-node", "active-power") is None


async def test_set_property_no_device(panel: SpanPanel) -> None:
    """Test set_property when no device/controller."""
    assert panel.set_property("uuid-node", "relay", "CLOSED") is False


async def test_description_property(panel: SpanPanel) -> None:
    """Test description property returns None when no device."""
    assert panel.description is None


async def test_available_default_false(panel: SpanPanel) -> None:
    """Test panel starts unavailable."""
    assert panel.available is False


async def test_on_device_discovered_filters_serial(
    panel: SpanPanel, mock_discovered_device
) -> None:
    """Test that _on_device_discovered ignores other devices."""
    other = MagicMock()
    other.device_id = "other-serial"
    panel._on_device_discovered(other)
    assert panel.device is None

    # Our device should be accepted
    panel._on_device_discovered(mock_discovered_device)
    assert panel.device is mock_discovered_device


async def test_on_description_received_sets_event(
    panel: SpanPanel, mock_discovered_device
) -> None:
    """Test that _on_description_received sets the description_received event."""
    assert not panel.description_received.is_set()

    # Simulate description received on paho thread → call_soon_threadsafe
    # In test we call it directly since hass.loop is the test event loop
    panel._on_description_received(mock_discovered_device)

    # The event should be set via call_soon_threadsafe
    # In tests, we need to yield to let call_soon_threadsafe execute
    await asyncio.sleep(0)
    assert panel.description_received.is_set()
