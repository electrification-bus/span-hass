"""Services for the SPAN Panel (eBus) integration."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

LINK_SUBPANEL_SCHEMA = vol.Schema({
    vol.Required("sub_serial"): str,
    vol.Required("parent_serial"): str,
})


async def async_setup_services(hass: HomeAssistant) -> None:
    """Register SPAN Panel services."""

    async def handle_link_subpanel(call: ServiceCall) -> None:
        """Set a sub-panel's parent in the device registry."""
        sub_serial: str = call.data["sub_serial"]
        parent_serial: str = call.data["parent_serial"]

        dev_reg = dr.async_get(hass)

        parent = dev_reg.async_get_device(identifiers={(DOMAIN, parent_serial)})
        if parent is None:
            raise HomeAssistantError(
                f"Parent panel with serial '{parent_serial}' not found"
            )

        sub = dev_reg.async_get_device(identifiers={(DOMAIN, sub_serial)})
        if sub is None:
            raise HomeAssistantError(
                f"Sub-panel with serial '{sub_serial}' not found"
            )

        dev_reg.async_update_device(sub.id, via_device_id=parent.id)
        _LOGGER.info(
            "Linked sub-panel %s → parent %s (via_device_id=%s)",
            sub_serial,
            parent_serial,
            parent.id,
        )

    hass.services.async_register(
        DOMAIN, "link_subpanel", handle_link_subpanel, schema=LINK_SUBPANEL_SCHEMA
    )
