"""Test fixtures for SPAN Panel (eBus) integration tests."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

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
)

MOCK_SERIAL = "nt-0000-abc12"
MOCK_HOST = "192.168.1.100"
MOCK_FIRMWARE = "spanos2/r202633/01"
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
