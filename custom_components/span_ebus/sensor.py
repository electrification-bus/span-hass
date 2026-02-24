"""Sensor platform for SPAN Panel (eBus) integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
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
    """Set up SPAN sensor entities from a config entry."""
    panel = hass.data[DOMAIN][entry.entry_id]["panel"]
    entity_specs: list[EntitySpec] = hass.data[DOMAIN][entry.entry_id]["entity_specs"]

    entities = [
        SpanEbusSensor(panel, spec)
        for spec in entity_specs
        if spec.platform == Platform.SENSOR
    ]

    if entities:
        async_add_entities(entities)
        _LOGGER.debug("Added %d sensor entities for %s", len(entities), panel.serial_number)


class SpanEbusSensor(SpanEbusEntity, SensorEntity):
    """A sensor entity for a SPAN Panel Homie property."""

    def __init__(self, panel: Any, spec: EntitySpec) -> None:
        """Initialize the sensor."""
        super().__init__(panel=panel, spec=spec)
        self._spec = spec

        self._attr_device_class = spec.device_class
        self._attr_state_class = spec.state_class
        self._attr_native_unit_of_measurement = spec.native_unit
        self._attr_entity_category = spec.entity_category
        if spec.icon:
            self._attr_icon = spec.icon

    _NUMERIC_DEVICE_CLASSES = {
        SensorDeviceClass.POWER,
        SensorDeviceClass.ENERGY,
        SensorDeviceClass.BATTERY,
        SensorDeviceClass.CURRENT,
        SensorDeviceClass.VOLTAGE,
        SensorDeviceClass.ENERGY_STORAGE,
    }

    def _update_from_value(self, value: str) -> None:
        """Update sensor state from a raw MQTT value."""
        if self.device_class in self._NUMERIC_DEVICE_CLASSES:
            try:
                numeric = float(value)
                if self._spec.negate:
                    numeric = -numeric
                self._attr_native_value = numeric
            except (ValueError, TypeError):
                self._attr_native_value = None
        elif self._property_id == "feed":
            # Feed values are circuit node ID references — resolve to name
            name = self._panel.get_property_value(value, "name")
            if name:
                self._attr_native_value = name
                self._attr_extra_state_attributes = {"circuit_id": value}
            else:
                self._attr_native_value = value
        else:
            self._attr_native_value = value
