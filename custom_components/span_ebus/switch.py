"""Switch platform for SPAN Panel (eBus) integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity_base import SpanEbusEntity
from .node_mappers import EntitySpec

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up SPAN switch entities from a config entry."""
    panel = hass.data[DOMAIN][entry.entry_id]["panel"]
    entity_specs: list[EntitySpec] = hass.data[DOMAIN][entry.entry_id]["entity_specs"]

    entities = [
        SpanEbusSwitch(panel, spec)
        for spec in entity_specs
        if spec.platform == Platform.SWITCH
    ]
    if entities:
        async_add_entities(entities)
        _LOGGER.debug("Added %d switch entities for %s", len(entities), panel.serial_number)


class SpanEbusSwitch(SpanEbusEntity, SwitchEntity):
    """A switch entity for a SPAN circuit relay (or shed override)."""

    def __init__(self, panel: Any, spec: EntitySpec) -> None:
        """Initialize the switch."""
        super().__init__(panel=panel, spec=spec)
        if spec.icon:
            self._attr_icon = spec.icon

    def _update_from_value(self, value: str) -> None:
        """Map publisher's enum / boolean state to HA's on/off."""
        # Circuit relay: CLOSED=on, OPEN=off. Shed override: true=on, false=off.
        upper = value.upper()
        if upper in {"CLOSED", "TRUE", "ON", "1", "YES"}:
            self._attr_is_on = True
        elif upper in {"OPEN", "FALSE", "OFF", "0", "NO"}:
            self._attr_is_on = False
        else:
            self._attr_is_on = None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Send the on-side value the publisher expects."""
        # Relay switches use CLOSED/OPEN; everything else (override etc.) takes a boolean.
        payload = "CLOSED" if self._property_id == "relay" else "true"
        self._panel.set_property(
            self._device_id, self._capability, self._property_id, payload
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Send the off-side value the publisher expects."""
        payload = "OPEN" if self._property_id == "relay" else "false"
        self._panel.set_property(
            self._device_id, self._capability, self._property_id, payload
        )
