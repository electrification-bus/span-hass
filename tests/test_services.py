"""Tests for the SPAN Panel (eBus) services."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from custom_components.span_ebus.const import DOMAIN
from custom_components.span_ebus.services import async_setup_services


@pytest.fixture
def hass():
    """Minimal mock HomeAssistant."""
    hass = MagicMock()
    hass.services = MagicMock()
    return hass


@pytest.fixture
def dev_reg():
    """Mock device registry with two panels."""
    reg = MagicMock()

    parent_device = MagicMock()
    parent_device.id = "dev-parent-001"

    sub_device = MagicMock()
    sub_device.id = "dev-sub-001"

    def _get_device(identifiers=None):
        if identifiers == {(DOMAIN, "nt-2024-a1b2c")}:
            return parent_device
        if identifiers == {(DOMAIN, "nt-2024-d3e4f")}:
            return sub_device
        return None

    reg.async_get_device = _get_device
    reg.async_update_device = MagicMock()
    return reg


@pytest.mark.asyncio
async def test_link_subpanel_service(hass, dev_reg):
    """Service finds devices by serial and sets via_device_id."""
    await async_setup_services(hass)

    # Extract the registered handler
    register_call = hass.services.async_register.call_args
    assert register_call[0][0] == DOMAIN
    assert register_call[0][1] == "link_subpanel"
    handler = register_call[0][2]

    # Build a mock ServiceCall
    call = MagicMock()
    call.data = {
        "sub_serial": "nt-2024-d3e4f",
        "parent_serial": "nt-2024-a1b2c",
    }

    with patch(
        "custom_components.span_ebus.services.dr.async_get", return_value=dev_reg
    ):
        await handler(call)

    dev_reg.async_update_device.assert_called_once_with(
        "dev-sub-001", via_device_id="dev-parent-001"
    )


@pytest.mark.asyncio
async def test_link_subpanel_missing_parent(hass, dev_reg):
    """Raises HomeAssistantError if parent serial not found."""
    from homeassistant.exceptions import HomeAssistantError

    await async_setup_services(hass)
    handler = hass.services.async_register.call_args[0][2]

    call = MagicMock()
    call.data = {
        "sub_serial": "nt-2024-d3e4f",
        "parent_serial": "nt-9999-missing",
    }

    with patch(
        "custom_components.span_ebus.services.dr.async_get", return_value=dev_reg
    ):
        with pytest.raises(HomeAssistantError, match="Parent panel.*not found"):
            await handler(call)


@pytest.mark.asyncio
async def test_link_subpanel_missing_sub(hass, dev_reg):
    """Raises HomeAssistantError if sub-panel serial not found."""
    from homeassistant.exceptions import HomeAssistantError

    await async_setup_services(hass)
    handler = hass.services.async_register.call_args[0][2]

    call = MagicMock()
    call.data = {
        "sub_serial": "nt-9999-missing",
        "parent_serial": "nt-2024-a1b2c",
    }

    with patch(
        "custom_components.span_ebus.services.dr.async_get", return_value=dev_reg
    ):
        with pytest.raises(HomeAssistantError, match="Sub-panel.*not found"):
            await handler(call)
