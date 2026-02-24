"""Binary sensor platform for SPAN Panel (eBus) integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity_base import SpanEbusEntity
from .node_mappers import EntitySpec

_LOGGER = logging.getLogger(__name__)

# Values that indicate "on" / True for boolean-typed binary sensors.
# Enum-typed binary sensors should use EntitySpec.on_values instead.
_TRUTHY = {"true", "1", "on", "yes", "connected", "active"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up SPAN binary sensor entities from a config entry."""
    panel = hass.data[DOMAIN][entry.entry_id]["panel"]
    entity_specs: list[EntitySpec] = hass.data[DOMAIN][entry.entry_id]["entity_specs"]

    entities = [
        SpanEbusBinarySensor(panel, spec)
        for spec in entity_specs
        if spec.platform == Platform.BINARY_SENSOR
    ]

    if entities:
        async_add_entities(entities)
        _LOGGER.debug(
            "Added %d binary sensor entities for %s", len(entities), panel.serial_number
        )


class SpanEbusBinarySensor(SpanEbusEntity, BinarySensorEntity):
    """A binary sensor entity for a SPAN Panel Homie property."""

    def __init__(self, panel: Any, spec: EntitySpec) -> None:
        """Initialize the binary sensor."""
        super().__init__(panel=panel, spec=spec)

        self._attr_device_class = spec.device_class
        self._attr_entity_category = spec.entity_category
        if spec.icon:
            self._attr_icon = spec.icon
        self._on_values = spec.on_values

    def _update_from_value(self, value: str) -> None:
        """Update binary sensor state from a raw MQTT value."""
        if self._on_values:
            self._attr_is_on = value.upper() in self._on_values
        else:
            self._attr_is_on = value.lower() in _TRUTHY
