"""Select platform for SPAN Panel (eBus) integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
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
    """Set up SPAN select entities from a config entry."""
    panel = hass.data[DOMAIN][entry.entry_id]["panel"]
    entity_specs: list[EntitySpec] = hass.data[DOMAIN][entry.entry_id]["entity_specs"]

    entities = [
        SpanEbusSelect(panel, spec)
        for spec in entity_specs
        if spec.platform == Platform.SELECT
    ]

    if entities:
        async_add_entities(entities)
        _LOGGER.debug("Added %d select entities for %s", len(entities), panel.serial_number)


class SpanEbusSelect(SpanEbusEntity, SelectEntity):
    """A select entity for a SPAN Panel circuit priority."""

    def __init__(self, panel: Any, spec: EntitySpec) -> None:
        """Initialize the select."""
        super().__init__(panel=panel, spec=spec)
        self._attr_options = spec.options
        if spec.icon:
            self._attr_icon = spec.icon

    def _update_from_value(self, value: str) -> None:
        """Update select state from a raw MQTT value."""
        if value in self._attr_options:
            self._attr_current_option = value
        else:
            _LOGGER.warning(
                "Received unknown option '%s' for %s (known: %s)",
                value,
                self._attr_unique_id,
                self._attr_options,
            )
            self._attr_current_option = value

    async def async_select_option(self, option: str) -> None:
        """Send selected option to the panel."""
        self._panel.set_property(self._node_id, self._property_id, option)
