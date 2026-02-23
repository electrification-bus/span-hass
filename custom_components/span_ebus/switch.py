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
    """A switch entity for a SPAN Panel circuit relay."""

    def __init__(self, panel: Any, spec: EntitySpec) -> None:
        """Initialize the switch."""
        super().__init__(panel=panel, spec=spec)
        if spec.icon:
            self._attr_icon = spec.icon

    def _update_from_value(self, value: str) -> None:
        """Update switch state from relay value (CLOSED=on, OPEN=off)."""
        self._attr_is_on = value.upper() == "CLOSED"

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the relay on (CLOSED)."""
        self._panel.set_property(self._node_id, self._property_id, "CLOSED")

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the relay off (OPEN)."""
        self._panel.set_property(self._node_id, self._property_id, "OPEN")
