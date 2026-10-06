"""Select platform for SPAN Panel (eBus) integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity_base import SpanEbusEntity, async_setup_platform_entities
from .node_mappers import EntitySpec

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up SPAN select entities from a config entry."""
    async_setup_platform_entities(
        hass, entry, Platform.SELECT, async_add_entities, SpanEbusSelect
    )


class SpanEbusSelect(SpanEbusEntity, SelectEntity):
    """A select entity for a SPAN enum (shed-priority etc.).

    Whether the property takes writes is per device: a circuit the panel manages
    itself publishes its priority without ``$settable``. The entity is created
    either way so the value still shows, and a write to a property the
    device's current description does not declare settable is refused. The
    gate is read live, so re-commissioning a circuit takes effect without
    reloading the integration.
    """

    def __init__(self, panel: Any, spec: EntitySpec) -> None:
        """Initialize the select."""
        super().__init__(panel=panel, spec=spec)
        self._attr_options = spec.options
        if spec.icon:
            self._attr_icon = spec.icon

    def _update_from_value(self, value: str) -> None:
        """Update the current_option from a raw MQTT value."""
        if value not in self._attr_options:
            _LOGGER.warning(
                "Unknown option '%s' for %s (known: %s)",
                value,
                self._attr_unique_id,
                self._attr_options,
            )
        self._attr_current_option = value

    async def async_select_option(self, option: str) -> None:
        """Send the selected option to the panel."""
        if not self._panel.is_property_settable(
            self._device_id, self._capability, self._property_id
        ):
            entity_id = self.entity_id or str(self._attr_unique_id)
            _LOGGER.debug(
                "Refusing option for %s: the panel does not declare it settable",
                entity_id,
            )
            raise ServiceValidationError(
                f"{entity_id} is read-only. The SPAN Panel does not accept a "
                "change to this setting on this circuit.",
                translation_domain=DOMAIN,
                translation_key="option_not_settable",
                translation_placeholders={"entity_id": entity_id},
            )
        self._panel.set_property(
            self._device_id, self._capability, self._property_id, option
        )
