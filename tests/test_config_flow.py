"""Tests for SPAN Panel (eBus) config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.span_ebus.api_client import SpanAuthError, SpanConnectionError
from custom_components.span_ebus.const import CONF_SERIAL_NUMBER, DOMAIN

from .conftest import (
    MOCK_CONFIG_DATA,
    MOCK_HOST,
    MOCK_SERIAL,
)


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations for all config flow tests."""


@pytest.fixture(autouse=True)
def _mock_setup_entry():
    """Prevent actual setup during config flow tests."""
    with patch(
        "custom_components.span_ebus.async_setup_entry",
        return_value=True,
    ):
        yield


@pytest.fixture(autouse=True)
def _patch_api_client(mock_api_client):
    """Patch SpanApiClient for all config flow tests."""
    with patch(
        "custom_components.span_ebus.config_flow.SpanApiClient",
        return_value=mock_api_client,
    ):
        yield


async def test_user_flow_passphrase(
    hass: HomeAssistant,
    mock_api_client,
) -> None:
    """Test full manual user flow with passphrase auth."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    # Enter host
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": MOCK_HOST}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "auth_menu"

    # Choose passphrase
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "auth_passphrase"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "auth_passphrase"

    # Enter passphrase
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"passphrase": "test-passphrase"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == f"SPAN Panel {MOCK_SERIAL}"
    assert result["data"][CONF_SERIAL_NUMBER] == MOCK_SERIAL


async def test_user_flow_door_bypass(
    hass: HomeAssistant,
    mock_api_client,
) -> None:
    """Test manual user flow with door bypass auth."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": MOCK_HOST}
    )
    assert result["type"] is FlowResultType.MENU

    # Choose door bypass
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "auth_door_bypass"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "auth_door_bypass"

    # Submit door bypass
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_cannot_connect(
    hass: HomeAssistant,
    mock_api_client,
) -> None:
    """Test user flow with connection error."""
    mock_api_client.get_status.side_effect = SpanConnectionError("Cannot connect")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": MOCK_HOST}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_invalid_passphrase(
    hass: HomeAssistant,
    mock_api_client,
) -> None:
    """Test passphrase auth with invalid passphrase."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": MOCK_HOST}
    )

    # Choose passphrase
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "auth_passphrase"}
    )

    # First attempt: invalid
    mock_api_client.register.side_effect = SpanAuthError("Invalid passphrase")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"passphrase": "wrong"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}

    # Second attempt: valid
    mock_api_client.register.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"passphrase": "correct"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_door_bypass_not_active(
    hass: HomeAssistant,
    mock_api_client,
) -> None:
    """Test door bypass when not active."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": MOCK_HOST}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "auth_door_bypass"}
    )

    mock_api_client.register.side_effect = SpanAuthError("Not active")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "door_bypass_not_active"}


async def test_zeroconf_discovery(
    hass: HomeAssistant,
    mock_api_client,
) -> None:
    """Test zeroconf discovery flow."""
    discovery_info = ZeroconfServiceInfo(
        ip_address="192.168.1.100",
        ip_addresses=["192.168.1.100"],
        port=8883,
        hostname="span-nt-0000-abc12.local.",
        type="_ebus._tcp.local.",
        name="span-nt-0000-abc12._ebus._tcp.local.",
        properties={},
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_ZEROCONF},
        data=discovery_info,
    )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "auth_menu"

    # Choose passphrase and complete
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "auth_passphrase"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"passphrase": "test"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_zeroconf_already_configured(
    hass: HomeAssistant,
    mock_api_client,
) -> None:
    """Test zeroconf discovery of already-configured panel."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=MOCK_CONFIG_DATA,
        unique_id=MOCK_SERIAL,
    )
    entry.add_to_hass(hass)

    discovery_info = ZeroconfServiceInfo(
        ip_address="192.168.1.100",
        ip_addresses=["192.168.1.100"],
        port=8883,
        hostname="span-nt-0000-abc12.local.",
        type="_ebus._tcp.local.",
        name="span-nt-0000-abc12._ebus._tcp.local.",
        properties={},
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_ZEROCONF},
        data=discovery_info,
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
