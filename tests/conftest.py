"""Test fixtures for SPAN Panel (eBus) integration tests."""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.core import HomeAssistant

from custom_components.span_ebus.api_client import AuthResponse, StatusResponse
from custom_components.span_ebus.const import (
    CONF_ACCESS_TOKEN,
    CONF_CA_CERT_PEM,
    CONF_EBUS_BROKER_HOST,
    CONF_EBUS_BROKER_PASSWORD,
    CONF_EBUS_BROKER_PORT,
    CONF_EBUS_BROKER_USERNAME,
    CONF_HOST,
    CONF_SERIAL_NUMBER,
    DOMAIN,
)

MOCK_SERIAL = "nt-0000-abc12"
MOCK_CIRCUIT_UUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
MOCK_HOST = "192.168.1.100"
MOCK_FIRMWARE = "spanos2/r202546/03"
MOCK_ACCESS_TOKEN = "test-access-token"
MOCK_BROKER_USERNAME = MOCK_SERIAL
MOCK_BROKER_PASSWORD = "test-broker-password"
MOCK_BROKER_HOST = f"span-{MOCK_SERIAL}.local"
MOCK_BROKER_PORT = 8883
MOCK_CA_CERT = "-----BEGIN CERTIFICATE-----\nTEST\n-----END CERTIFICATE-----"

MOCK_CONFIG_DATA = {
    CONF_HOST: MOCK_HOST,
    CONF_SERIAL_NUMBER: MOCK_SERIAL,
    CONF_ACCESS_TOKEN: MOCK_ACCESS_TOKEN,
    CONF_EBUS_BROKER_USERNAME: MOCK_BROKER_USERNAME,
    CONF_EBUS_BROKER_PASSWORD: MOCK_BROKER_PASSWORD,
    CONF_EBUS_BROKER_HOST: MOCK_BROKER_HOST,
    CONF_EBUS_BROKER_PORT: MOCK_BROKER_PORT,
    CONF_CA_CERT_PEM: MOCK_CA_CERT,
}


MOCK_DESCRIPTION = {
    "homie": "5.0",
    "version": 1739900000000,
    "type": "SPAN Panel",
    "name": "SPAN Main Panel",
    "nodes": {
        "core": {
            "name": "Core",
            "type": "energy.ebus.device.distribution-enclosure.core",
            "properties": {
                "door": {
                    "name": "Door",
                    "datatype": "boolean",
                    "settable": False,
                    "retained": True,
                },
                "ethernet": {
                    "name": "Ethernet",
                    "datatype": "boolean",
                    "settable": False,
                    "retained": True,
                },
                "software-version": {
                    "name": "Software Version",
                    "datatype": "string",
                    "settable": False,
                    "retained": True,
                },
                "dominant-power-source": {
                    "name": "Dominant Power Source",
                    "datatype": "enum",
                    "format": "GRID,BATTERY,PV,GENERATOR,NONE,UNKNOWN",
                    "settable": True,
                    "retained": True,
                },
                "relay": {
                    "name": "Main Relay",
                    "datatype": "enum",
                    "format": "UNKNOWN,OPEN,CLOSED",
                    "settable": False,
                    "retained": True,
                },
            },
        },
        MOCK_CIRCUIT_UUID: {
            "name": "Circuit 1",
            "type": "energy.ebus.device.circuit",
            "properties": {
                "relay": {
                    "name": "Relay",
                    "datatype": "enum",
                    "format": "OPEN,CLOSED",
                    "settable": True,
                    "retained": True,
                },
                "active-power": {
                    "name": "Active Power",
                    "datatype": "float",
                    "unit": "kW",
                    "settable": False,
                    "retained": True,
                },
                "imported-energy": {
                    "name": "Imported Energy",
                    "datatype": "float",
                    "unit": "Wh",
                    "settable": False,
                    "retained": True,
                },
                "exported-energy": {
                    "name": "Exported Energy",
                    "datatype": "float",
                    "unit": "Wh",
                    "settable": False,
                    "retained": True,
                },
                "shed-priority": {
                    "name": "Shed Priority",
                    "datatype": "enum",
                    "format": "MUST_HAVE,NICE_TO_HAVE,NON_ESSENTIAL",
                    "settable": True,
                    "retained": True,
                },
                "name": {
                    "name": "Name",
                    "datatype": "string",
                    "settable": False,
                    "retained": True,
                },
                "space": {
                    "name": "Space",
                    "datatype": "integer",
                    "format": "1:32:1",
                    "settable": False,
                    "retained": True,
                },
                "pcs-priority": {
                    "name": "PCS Priority",
                    "datatype": "integer",
                    "settable": False,
                    "retained": True,
                },
                "dipole": {
                    "name": "Dipole",
                    "datatype": "boolean",
                    "settable": False,
                    "retained": True,
                },
            },
        },
        "power-flows": {
            "name": "Power Flows",
            "type": "energy.ebus.device.power-flows",
            "properties": {
                "grid-power": {
                    "name": "Grid Power",
                    "datatype": "integer",
                    "unit": "W",
                    "settable": False,
                    "retained": True,
                },
                "solar-production": {
                    "name": "Solar Production",
                    "datatype": "integer",
                    "unit": "W",
                    "settable": False,
                    "retained": True,
                },
            },
        },
    },
}


@pytest.fixture
def mock_status_response() -> StatusResponse:
    return StatusResponse(
        serial_number=MOCK_SERIAL,
        firmware_version=MOCK_FIRMWARE,
    )


@pytest.fixture
def mock_auth_response() -> AuthResponse:
    return AuthResponse(
        access_token=MOCK_ACCESS_TOKEN,
        serial_number=MOCK_SERIAL,
        ebus_broker_username=MOCK_BROKER_USERNAME,
        ebus_broker_password=MOCK_BROKER_PASSWORD,
        ebus_broker_host=MOCK_BROKER_HOST,
        ebus_broker_mqtts_port=MOCK_BROKER_PORT,
    )


@pytest.fixture
def mock_api_client(mock_status_response, mock_auth_response):
    """Create a mock SpanApiClient."""
    client = AsyncMock()
    client.get_status = AsyncMock(return_value=mock_status_response)
    client.register = AsyncMock(return_value=mock_auth_response)
    client.get_ca_certificate = AsyncMock(return_value=MOCK_CA_CERT)
    client.close = AsyncMock()
    return client


@pytest.fixture
def mock_controller():
    """Create a mock ebus_sdk Controller."""
    controller = MagicMock()
    controller.start_discovery = MagicMock()
    controller.stop = MagicMock()
    controller.set_property = MagicMock(return_value=True)
    controller.get_device = MagicMock(return_value=None)
    controller.get_all_devices = MagicMock(return_value={})

    # Store callbacks so tests can trigger them
    controller._callbacks = {}

    def store_callback(name):
        def setter(cb):
            controller._callbacks[name] = cb
        return setter

    controller.set_on_device_discovered_callback = store_callback("device_discovered")
    controller.set_on_description_received_callback = store_callback("description_received")
    controller.set_on_property_changed_callback = store_callback("property_changed")
    controller.set_on_device_state_changed_callback = store_callback("device_state_changed")

    return controller


@pytest.fixture
def mock_discovered_device():
    """Create a mock DiscoveredDevice."""
    device = MagicMock()
    device.device_id = MOCK_SERIAL
    device.state = "ready"
    device.description = MOCK_DESCRIPTION
    device.properties = {}

    def get_property(node_id, prop_id):
        return device.properties.get(f"{node_id}/{prop_id}")

    device.get_property = get_property
    return device
