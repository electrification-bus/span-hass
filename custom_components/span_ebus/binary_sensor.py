"""Binary sensor platform for SPAN Panel (eBus) integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity_base import SpanEbusEntity, async_setup_platform_entities
from .node_mappers import EntitySpec

# Default truthy values for boolean-typed binary sensors. Enum-typed sensors
# should use EntitySpec.on_values to scope precisely.
_TRUTHY = {"true", "1", "on", "yes", "connected", "active"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up SPAN binary sensor entities from a config entry."""
    async_setup_platform_entities(
        hass, entry, Platform.BINARY_SENSOR, async_add_entities, SpanEbusBinarySensor
    )


class SpanEbusBinarySensor(SpanEbusEntity, BinarySensorEntity):
    """A binary sensor entity for a SPAN boolean or PROBLEM-class enum property."""

    def __init__(self, panel: Any, spec: EntitySpec) -> None:
        """Initialize the binary sensor."""
        super().__init__(panel=panel, spec=spec)
        self._attr_device_class = spec.device_class
        self._attr_entity_category = spec.entity_category
        if spec.icon:
            self._attr_icon = spec.icon
        self._on_values = spec.on_values

    def _update_from_value(self, value: str) -> None:
        """Map publisher's enum / boolean state to HA's on/off."""
        if self._on_values:
            self._attr_is_on = value.upper() in self._on_values
        else:
            self._attr_is_on = value.lower() in _TRUTHY
